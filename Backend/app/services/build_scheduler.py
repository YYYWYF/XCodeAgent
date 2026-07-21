from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

TERMINAL_STATUSES = {"completed", "failed"}
RUNNABLE_STATUS = "pending"
RETRYABLE_FAILURES = {
    "runner_crash",
    "timeout",
    "tool_error",
    "model_error",
    "sandbox_error",
    "network_error",
}
REPAIRABLE_FAILURES = {
    "compile_error",
    "type_error",
    "test_failure",
    "lint_failure",
    "runtime_error",
    "acceptance_failed",
    "no_file_changes",
}
CONFIRMATION_FAILURES = {
    "contract_mismatch",
    "plan_mismatch",
    "workspace_snapshot_stale",
}


def select_ready_build_batch(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Select the next dependency-ready, lock-compatible task batch."""

    tasks_by_id = {str(task.get("id")): task for task in tasks if task.get("id")}
    completed = {
        task_id
        for task_id, task in tasks_by_id.items()
        if task.get("status", RUNNABLE_STATUS) == "completed"
    }
    failed = {
        task_id
        for task_id, task in tasks_by_id.items()
        if task.get("status") == "failed"
    }
    pending = [
        task
        for task in tasks_by_id.values()
        if task.get("status", RUNNABLE_STATUS) == RUNNABLE_STATUS
    ]
    missing_dependency_errors = _missing_dependency_errors(pending, tasks_by_id)
    dependency_failed = [
        task for task in pending if set(_dependencies(task)).intersection(failed)
    ]
    ready_candidates = [
        task
        for task in pending
        if not set(_dependencies(task)).intersection(failed)
        and set(_dependencies(task)).issubset(completed)
        and not any(
            error.startswith(f"Task {task['id']} ")
            for error in missing_dependency_errors
        )
    ]

    selected = _lock_compatible_batch(ready_candidates)
    return {
        "ready_tasks": selected,
        "ready_task_ids": [task["id"] for task in selected],
        "blocked_tasks": [
            {
                "id": task["id"],
                "reason": "dependency_failed",
                "failed_dependencies": sorted(
                    set(_dependencies(task)).intersection(failed)
                ),
            }
            for task in dependency_failed
        ],
        "errors": missing_dependency_errors,
        "is_idle": not selected,
        "is_complete": bool(tasks_by_id)
        and all(task.get("status") == "completed" for task in tasks_by_id.values()),
    }


def mark_tasks_running(
    tasks: list[dict[str, Any]],
    ready_task_ids: list[str],
) -> list[dict[str, Any]]:
    now = datetime.now(UTC).isoformat()
    ready = set(ready_task_ids)
    return [
        (
            {
                **task,
                "status": "running",
                "scheduler": {
                    **(
                        task.get("scheduler")
                        if isinstance(task.get("scheduler"), dict)
                        else {}
                    ),
                    "started_at": now,
                },
            }
            if task.get("id") in ready
            else task
        )
        for task in tasks
    ]


def normalize_task_results(
    *,
    dispatched_tasks: list[dict[str, Any]],
    raw_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate runner output and convert malformed/missing results into failures."""

    dispatched_by_id = {task["id"]: task for task in dispatched_tasks}
    results_by_task_id = {
        str(result.get("task_id")): result
        for result in raw_results
        if isinstance(result, dict) and result.get("task_id")
    }
    normalized: list[dict[str, Any]] = []
    for task_id, task in dispatched_by_id.items():
        result = results_by_task_id.get(task_id)
        if result is None:
            normalized.append(
                _protocol_failure(
                    task, "Runner did not return a result for the dispatched task."
                )
            )
            continue
        status = str(result.get("status") or "").strip()
        if status not in {"completed", "failed"}:
            normalized.append(
                _protocol_failure(
                    task, f"Runner returned invalid status: {status or '<empty>'}."
                )
            )
            continue
        normalized.append(
            {
                **result,
                "task_id": task_id,
                "owner": result.get("owner") or task.get("owner"),
                "scheduler_decision": classify_task_result(result),
            }
        )
    return normalized


def classify_task_result(result: dict[str, Any]) -> dict[str, str]:
    if result.get("status") == "completed":
        return {"action": "complete", "reason": "runner_completed"}

    category = str(
        result.get("failure_category")
        or result.get("error_category")
        or result.get("category")
        or "implementation_failure"
    )
    if category in RETRYABLE_FAILURES:
        return {"action": "retry", "reason": category}
    if category in REPAIRABLE_FAILURES or category == "implementation_failure":
        return {"action": "repair", "reason": category}
    if category in CONFIRMATION_FAILURES:
        return {"action": "requires_confirmation", "reason": category}
    return {"action": "terminal_failure", "reason": category}


def verify_task_file_changes(
    *,
    results: list[dict[str, Any]],
    code_change_set: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """验证 agent 是否实际写入了文件。

    如果 code_change_set 为 None 或无文件变更，将 "completed" 结果改为
    "failed"（failure_category="no_file_changes"），避免幻影完成。
    否则将实际变更的文件路径填入 changed_files，替换 create_agent_task_result
    中硬编码的空列表。
    """

    changed_paths: list[str] = []
    if code_change_set and isinstance(code_change_set.get("files"), list):
        changed_paths = [
            str(f.get("path", ""))
            for f in code_change_set["files"]
            if isinstance(f, dict) and f.get("path")
        ]

    verified: list[dict[str, Any]] = []
    for result in results:
        verified_result = dict(result)
        if verified_result.get("status") != "completed":
            verified.append(verified_result)
            continue

        if not changed_paths:
            verified_result["status"] = "failed"
            verified_result["failure_category"] = "no_file_changes"
            original_note = verified_result.get("agent_note", "")
            suffix = (
                "VERIFICATION FAILED: Agent reported completion but no files "
                "were written to the workspace. Expected file changes for this task."
            )
            verified_result["agent_note"] = (
                f"{original_note}\n\n{suffix}" if original_note else suffix
            )
            verified_result["scheduler_decision"] = classify_task_result(verified_result)
        else:
            verified_result["changed_files"] = changed_paths
        verified.append(verified_result)
    return verified


def summarize_build_runtime(
    tasks: list[dict[str, Any]],
    build_results: list[dict[str, Any]],
) -> dict[str, Any]:
    counts = defaultdict(int)
    for task in tasks:
        counts[str(task.get("status") or RUNNABLE_STATUS)] += 1
    failed_results = [
        result for result in build_results if result.get("status") == "failed"
    ]
    repairable = [
        result
        for result in failed_results
        if (result.get("scheduler_decision") or {}).get("action") == "repair"
    ]
    confirmation = [
        result
        for result in failed_results
        if (result.get("scheduler_decision") or {}).get("action")
        == "requires_confirmation"
    ]
    return {
        "total": len(tasks),
        "completed": counts["completed"],
        "failed": counts["failed"],
        "pending": counts["pending"],
        "running": counts["running"],
        "results": len(build_results),
        "repairable_failures": len(repairable),
        "requires_confirmation": len(confirmation),
        "status": _overall_status(tasks, repairable, confirmation),
    }


def _overall_status(
    tasks: list[dict[str, Any]],
    repairable: list[dict[str, Any]],
    confirmation: list[dict[str, Any]],
) -> str:
    if tasks and all(task.get("status") == "completed" for task in tasks):
        return "completed"
    if confirmation:
        return "requires_confirmation"
    if repairable:
        return "needs_repair"
    if any(task.get("status") == "failed" for task in tasks):
        return "failed"
    return "in_progress"


def _dependencies(task: dict[str, Any]) -> list[str]:
    value = task.get("dependencies") or task.get("dependsOn") or []
    return (
        [str(item) for item in value if str(item).strip()]
        if isinstance(value, list)
        else []
    )


def _missing_dependency_errors(
    tasks: list[dict[str, Any]],
    tasks_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    return [
        f"Task {task.get('id')} depends on missing task {dependency}."
        for task in tasks
        for dependency in _dependencies(task)
        if dependency not in tasks_by_id
    ]


def _lock_compatible_batch(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not tasks:
        return []
    serial = [
        task
        for task in tasks
        if not bool(task.get("can_run_in_parallel", task.get("canRunInParallel", True)))
    ]
    if serial:
        return [sorted(serial, key=_task_sort_key)[0]]

    selected: list[dict[str, Any]] = []
    used_locks: set[str] = set()
    for task in sorted(tasks, key=_task_sort_key):
        locks = set(_task_locks(task))
        if locks and used_locks.intersection(locks):
            continue
        selected.append(task)
        used_locks.update(locks)
    return selected or [sorted(tasks, key=_task_sort_key)[0]]


def _task_locks(task: dict[str, Any]) -> list[str]:
    locks = _string_list(task.get("lock_scope"))
    if locks:
        return locks
    change_scope = task.get("change_scope")
    if isinstance(change_scope, list):
        locks = [
            str(item.get("path"))
            for item in change_scope
            if isinstance(item, dict) and item.get("path")
        ]
    locks.extend(_string_list(task.get("targetFiles") or task.get("target_files")))
    locks.extend(_string_list(task.get("allowed_paths") or task.get("allowedPaths")))
    return sorted({lock for lock in locks if lock})


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _task_sort_key(task: dict[str, Any]) -> tuple[int, str]:
    owner_priority = {"data_source": 0, "frontend": 1}
    return (owner_priority.get(str(task.get("owner")), 9), str(task.get("id")))


def _protocol_failure(task: dict[str, Any], reason: str) -> dict[str, Any]:
    result = {
        "task_id": task["id"],
        "owner": task.get("owner"),
        "status": "failed",
        "failure_category": "runner_protocol_error",
        "agent_note": reason,
        "changed_files": [],
        "commands": [],
        "change_request": None,
    }
    result["scheduler_decision"] = classify_task_result(result)
    return result
