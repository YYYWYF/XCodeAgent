from __future__ import annotations

from collections import defaultdict
from fnmatch import fnmatch
from datetime import UTC, datetime
from typing import Any

from app.services.engineering_acceptance import ensure_engineering_acceptance
from app.services.engineering_acceptance_verifier import verify_engineering_acceptance

TERMINAL_STATUSES = {"completed", "failed", "already_satisfied"}
RUNNABLE_STATUS = "pending"
RETRYABLE_FAILURES = {
    "invalid_structured_response",
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
    "acceptance_verification_failed",
    "business_acceptance_failed",
    "no_file_changes",
}
CONFIRMATION_FAILURES = {
    "contract_mismatch",
    "plan_mismatch",
    "workspace_snapshot_stale",
    "database_approval_required",
}


def select_ready_build_batch(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """选择下一批依赖满足且文件锁兼容的构建任务。"""

    tasks_by_id = {str(task.get("id")): task for task in tasks if task.get("id")}
    completed = {
        task_id
        for task_id, task in tasks_by_id.items()
        if task.get("status", RUNNABLE_STATUS) in {"completed", "already_satisfied"}
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

    selected = _lock_compatible_batch(_database_first_candidates(ready_candidates, tasks_by_id))
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
        and all(
            task.get("status") in {"completed", "already_satisfied"}
            for task in tasks_by_id.values()
        ),
    }


def mark_tasks_running(
    tasks: list[dict[str, Any]],
    ready_task_ids: list[str],
) -> list[dict[str, Any]]:
    """将本轮派发的任务标记为 running，并记录调度开始时间。"""

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
    """校验 runner 输出，并将缺失或畸形结果转为失败记录。"""

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
        if status not in {"completed", "already_satisfied", "failed"}:
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
    """把任务失败类型归类为重试、修复、确认或终止失败。"""

    if result.get("status") == "completed":
        return {"action": "complete", "reason": "runner_completed"}
    if result.get("status") == "already_satisfied":
        return {"action": "complete", "reason": "already_satisfied"}

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


def retryable_failed_task_ids(
    tasks: list[dict[str, Any]],
    build_results: list[dict[str, Any]],
) -> set[str]:
    """找出当前切片中最近一次结果属于 retry 分类的失败任务。"""

    latest_results = _latest_task_results(build_results)
    retryable_ids: set[str] = set()
    for task in tasks:
        task_id = str(task.get("id") or "").strip()
        if not task_id or task.get("status") != "failed":
            continue
        result = latest_results.get(task_id)
        if result is None:
            failure_detail = task.get("failure_detail")
            scheduler_decision = (
                failure_detail.get("scheduler_decision")
                if isinstance(failure_detail, dict)
                else None
            )
            result = {
                "status": "failed",
                "failure_category": task.get("failure_category"),
                "scheduler_decision": scheduler_decision,
            }
        decision = result.get("scheduler_decision")
        action = decision.get("action") if isinstance(decision, dict) else ""
        if action == "retry" or classify_task_result(result).get("action") == "retry":
            retryable_ids.add(task_id)
    return retryable_ids


def reset_failed_tasks_for_retry(
    tasks: list[dict[str, Any]],
    task_ids: set[str],
) -> list[dict[str, Any]]:
    """只把指定的可重试失败任务恢复为 pending，并保留重试审计信息。"""

    if not task_ids:
        return tasks

    now = datetime.now(UTC).isoformat()
    reset_tasks: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        if task_id not in task_ids or task.get("status") != "failed":
            reset_tasks.append(task)
            continue
        try:
            retry_count = max(int(task.get("retry_count", 0) or 0), 0) + 1
        except (TypeError, ValueError):
            # 恢复快照来自客户端，非法重试计数不能阻断本次受控恢复。
            retry_count = 1
        scheduler = task.get("scheduler") if isinstance(task.get("scheduler"), dict) else {}
        reset_task = dict(task)
        reset_task.update(
            {
                "status": RUNNABLE_STATUS,
                "retry_count": retry_count,
                "scheduler": {
                    **scheduler,
                    "last_action": "retry_failed_tasks",
                    "retry_count": retry_count,
                    "retry_at": now,
                },
                "updated_by": "build-scheduler-retry",
                "updated_at": now,
            }
        )
        for field in (
            "last_result_status",
            "failure_category",
            "failure_reason",
            "failure_detail",
        ):
            reset_task.pop(field, None)
        reset_tasks.append(reset_task)
    return reset_tasks


def _latest_task_results(build_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按结果流顺序保留每个任务的最后一次尝试，避免旧失败污染当前摘要。"""

    latest: dict[str, dict[str, Any]] = {}
    for result in build_results:
        task_id = str(result.get("task_id") or "").strip()
        if task_id:
            latest[task_id] = result
    return latest


def hydrate_missing_failed_results(
    tasks: list[dict[str, Any]],
    build_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从持久化任务状态补齐 checkpoint 中缺失的失败结果记录。"""

    latest_results = _latest_task_results(build_results)
    hydrated_results = list(build_results)
    for task in tasks:
        task_id = str(task.get("id") or "").strip()
        if not task_id or task.get("status") != "failed" or task_id in latest_results:
            continue
        failure_detail = task.get("failure_detail")
        detail = failure_detail if isinstance(failure_detail, dict) else {}
        result = {
            "task_id": task_id,
            "owner": task.get("owner"),
            "status": "failed",
            "failure_category": task.get("failure_category") or "implementation_failure",
            "failure_reason": task.get("failure_reason") or detail.get("failure_reason"),
            "agent_note": task.get("failure_reason") or detail.get("agent_note"),
            "changed_files": detail.get("changed_files", []),
            "commands": detail.get("commands", []),
        }
        result["scheduler_decision"] = (
            detail.get("scheduler_decision")
            if isinstance(detail.get("scheduler_decision"), dict)
            else classify_task_result(result)
        )
        hydrated_results.append(result)
        latest_results[task_id] = result
    return hydrated_results


def ready_repair_task_ids(repair_task_plan: dict[str, Any] | None) -> set[str]:
    """读取待执行修复计划中的未完成任务，作为失败恢复入口的候选。"""

    if not isinstance(repair_task_plan, dict):
        return set()
    if repair_task_plan.get("decision", "repair") != "repair":
        return set()
    if repair_task_plan.get("status", "ready") in {
        "terminal_failure",
        "requires_user_confirmation",
    }:
        return set()
    tasks = repair_task_plan.get("tasks")
    if not isinstance(tasks, list):
        return set()
    return {
        str(task.get("id"))
        for task in tasks
        if isinstance(task, dict)
        and str(task.get("id") or "").strip()
        and task.get("status", RUNNABLE_STATUS)
        not in {"completed", "already_satisfied"}
    }


def verify_task_file_changes(
    *,
    results: list[dict[str, Any]],
    code_change_set: dict[str, Any] | None,
    tasks: list[dict[str, Any]] | None = None,
    workspace_root: str | None = None,
    batch_unauthorized_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    """在 Owner 执行后执行完整工程验收并生成结构化工程证据。"""

    changed_paths: list[str] = []
    if code_change_set and isinstance(code_change_set.get("files"), list):
        changed_paths = [
            str(f.get("path", ""))
            for f in code_change_set["files"]
            if isinstance(f, dict) and f.get("path")
        ]

    tasks_by_id = {
        str(task.get("id")): task
        for task in tasks or []
        if task.get("id")
    }
    verified: list[dict[str, Any]] = []
    for result in results:
        verified_result = dict(result)
        status = verified_result.get("status")
        if status not in {"completed", "already_satisfied"}:
            verified.append(verified_result)
            continue

        task = ensure_engineering_acceptance(
            tasks_by_id.get(str(verified_result.get("task_id") or ""), {})
        )
        authorized_paths = _task_authorized_paths(task)
        attributed_paths = [
            path for path in changed_paths if _path_matches_any(path, authorized_paths)
        ]
        acceptance_evidence, acceptance_errors = verify_engineering_acceptance(
            task=task,
            status=str(status),
            code_change_set=code_change_set,
            workspace_root=workspace_root,
            batch_unauthorized_paths=batch_unauthorized_paths,
        )
        verified_result["acceptance_evidence"] = acceptance_evidence
        verified_result["acceptance_status"] = {
            **(
                verified_result.get("acceptance_status")
                if isinstance(verified_result.get("acceptance_status"), dict)
                else {}
            ),
            "engineering": "failed" if acceptance_errors else "passed",
        }
        verified_result["satisfaction_evidence"] = {
            "target_files": _task_target_paths(task),
            "acceptance_checks": acceptance_evidence,
        }
        if acceptance_errors:
            _mark_acceptance_failure(verified_result, acceptance_errors)
        else:
            verified_result["status"] = status
            verified_result["changed_files"] = attributed_paths
            verified_result["failure_category"] = None
            verified_result["failure_reason"] = None
            verified_result["scheduler_decision"] = classify_task_result(verified_result)
        verified.append(verified_result)
    return verified


def attribute_task_file_changes(
    *,
    results: list[dict[str, Any]],
    code_change_set: dict[str, Any] | None,
    tasks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """仅把真实文件差异归属到任务，不执行 engineering_acceptance 校验。"""

    changed_paths: list[str] = []
    if code_change_set and isinstance(code_change_set.get("files"), list):
        changed_paths = [
            str(file_item.get("path", ""))
            for file_item in code_change_set["files"]
            if isinstance(file_item, dict) and file_item.get("path")
        ]

    tasks_by_id = {
        str(task.get("id")): task
        for task in tasks or []
        if task.get("id")
    }
    attributed: list[dict[str, Any]] = []
    for result in results:
        attributed_result = dict(result)
        if result.get("status") in {"completed", "already_satisfied"}:
            task = tasks_by_id.get(str(result.get("task_id") or ""), {})
            authorized_paths = _task_authorized_paths(task)
            attributed_result["changed_files"] = [
                path
                for path in changed_paths
                if _path_matches_any(path, authorized_paths)
            ]
        attributed.append(attributed_result)
    return attributed


def _mark_acceptance_failure(
    result: dict[str, Any],
    errors: list[str],
) -> None:
    """把工程验收失败原地转换为可交给 RepairPlanner 的失败结果。"""

    result["status"] = "failed"
    result["failure_category"] = "acceptance_verification_failed"
    result["failure_reason"] = "工程验收未通过：" + "；".join(errors)
    original_note = str(result.get("agent_note") or "")
    suffix = f"VERIFICATION FAILED: {result['failure_reason']}"
    result["agent_note"] = f"{original_note}\n\n{suffix}" if original_note else suffix
    result["scheduler_decision"] = classify_task_result(result)


def _task_target_paths(task: dict[str, Any]) -> list[str]:
    """读取工程验收证据需要覆盖的精确目标文件。"""

    return list(
        dict.fromkeys(
            _normalize_path(path)
            for path in task.get("target_files", [])
            if _normalize_path(path)
        )
    )


def _task_authorized_paths(task: dict[str, Any]) -> list[str]:
    """汇总单个任务声明的精确或通配授权路径。"""

    paths = [str(path) for path in task.get("allowed_paths", []) if str(path).strip()]
    paths.extend(
        str(change.get("path"))
        for change in task.get("change_scope", [])
        if isinstance(change, dict) and change.get("path")
    )
    paths.extend(
        str(path)
        for path in task.get("target_files") or []
        if str(path).strip()
    )
    return list(dict.fromkeys(_normalize_path(path) for path in paths if _normalize_path(path)))


def _path_matches_any(path: str, patterns: list[str]) -> bool:
    """判断实际变更路径是否落在任务授权范围内。"""

    normalized = _normalize_path(path).casefold()
    for pattern in patterns:
        normalized_pattern = _normalize_path(pattern).casefold()
        if normalized_pattern.endswith("/**"):
            if normalized.startswith(normalized_pattern[:-3].rstrip("/") + "/"):
                return True
        elif normalized == normalized_pattern or normalized.startswith(normalized_pattern.rstrip("/") + "/") or fnmatch(normalized, normalized_pattern):
            return True
    return False


def _normalize_path(value: Any) -> str:
    """统一批次差异和任务授权中的跨平台相对路径。"""

    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return "/".join(part for part in text.split("/") if part not in {"", "."})


def summarize_build_runtime(
    tasks: list[dict[str, Any]],
    build_results: list[dict[str, Any]],
    *,
    repair_task_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """汇总当前执行切片内任务的运行状态和可用失败恢复入口。"""

    counts = defaultdict(int)
    for task in tasks:
        counts[str(task.get("status") or RUNNABLE_STATUS)] += 1
    latest_results = _latest_task_results(build_results)
    # 修复任务成功后父任务会被关闭为 completed；旧失败结果只作审计记录，不能再次触发 RepairPlanner。
    active_failed_task_ids = {
        str(task.get("id") or "")
        for task in tasks
        if task.get("status") == "failed"
    }
    failed_results = [
        result
        for result in latest_results.values()
        if result.get("status") == "failed"
        and str(result.get("task_id") or "") in active_failed_task_ids
    ]
    retryable = [
        result
        for result in failed_results
        if classify_task_result(result).get("action") == "retry"
    ]
    repairable = [
        result
        for result in failed_results
        if classify_task_result(result).get("action") == "repair"
    ]
    confirmation = [
        result
        for result in failed_results
        if classify_task_result(result).get("action") == "requires_confirmation"
    ]
    repair_task_ids = sorted(ready_repair_task_ids(repair_task_plan))
    return {
        "total": len(tasks),
        "completed": counts["completed"] + counts.get("already_satisfied", 0),
        "failed": counts["failed"],
        "pending": counts["pending"],
        "running": counts["running"],
        "already_satisfied": counts.get("already_satisfied", 0),
        "results": len(build_results),
        "retryable_failures": len(retryable),
        "retryable_task_ids": sorted(
            str(result.get("task_id"))
            for result in retryable
            if result.get("task_id")
        ),
        "repairable_failures": len(repairable),
        "requires_confirmation": len(confirmation),
        "recovery_available": bool(retryable or repair_task_ids),
        "recovery_task_ids": repair_task_ids,
        "retry_available": bool(retryable and not repairable and not confirmation),
        "status": _overall_status(tasks, repairable, confirmation),
    }


def resolve_execution_slice(
    *,
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    build_execution_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按应用、页面或数据源范围裁剪 BuildScheduler 的可执行任务视图。"""

    scope = _normalized_scope(build_execution_scope)
    unit_ids = _execution_unit_ids(build_task_plan, scope)
    task_ids = [
        str(task.get("id"))
        for task in tasks
        if task.get("id") and str(task.get("unit_id") or "application:root") in unit_ids
    ]
    task_id_set = set(task_ids)
    sliced_tasks = [task for task in tasks if str(task.get("id") or "") in task_id_set]
    reusable_task_ids = [
        str(task.get("id"))
        for task in sliced_tasks
        if task.get("status") in {"completed", "already_satisfied"}
    ]
    pending_task_ids = [
        str(task.get("id"))
        for task in sliced_tasks
        if task.get("status", RUNNABLE_STATUS) == RUNNABLE_STATUS
    ]
    return {
        "scope": scope,
        "target_unit_ids": _target_unit_ids(scope),
        "unit_ids": unit_ids,
        "task_ids": task_ids,
        "pending_task_ids": pending_task_ids,
        "reusable_task_ids": reusable_task_ids,
        "tasks": sliced_tasks,
        "summary": {
            "total": len(sliced_tasks),
            "pending": len(pending_task_ids),
            "running": len([task for task in sliced_tasks if task.get("status") == "running"]),
            "reused": len(reusable_task_ids),
            "completed": len(
                [
                    task
                    for task in sliced_tasks
                    if task.get("status") in {"completed", "already_satisfied"}
                ]
            ),
            "failed": len([task for task in sliced_tasks if task.get("status") == "failed"]),
        },
    }


def _overall_status(
    tasks: list[dict[str, Any]],
    repairable: list[dict[str, Any]],
    confirmation: list[dict[str, Any]],
) -> str:
    """根据切片任务和失败分类计算构建总体状态。"""

    if tasks and all(
        task.get("status") in {"completed", "already_satisfied"} for task in tasks
    ):
        return "completed"
    if confirmation:
        return "requires_confirmation"
    if repairable:
        return "needs_repair"
    if any(task.get("status") == "failed" for task in tasks):
        return "failed"
    return "in_progress"


def _dependencies(task: dict[str, Any]) -> list[str]:
    """读取当前 DAG v3 任务的依赖 ID。"""

    value = task.get("dependencies") or []
    return (
        [str(item) for item in value if str(item).strip()]
        if isinstance(value, list)
        else []
    )


def _missing_dependency_errors(
    tasks: list[dict[str, Any]],
    tasks_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    """检查当前可见任务集中是否存在缺失依赖。"""

    return [
        f"Task {task.get('id')} depends on missing task {dependency}."
        for task in tasks
        for dependency in _dependencies(task)
        if dependency not in tasks_by_id
    ]


def _lock_compatible_batch(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从就绪任务中选择文件锁不冲突的一批任务。

    can_run_in_parallel=False 的任务（共享文件/公共契约/文件冲突）只约束与触碰同
    文件锁的任务串行，不阻止文件锁互斥的任务并行。这样前后端任务即使一方被标
    can_parallel=False，只要文件锁不冲突仍可同批并行派发。
    """

    if not tasks:
        return []

    selected: list[dict[str, Any]] = []
    used_locks: set[str] = set()
    for task in sorted(tasks, key=_task_sort_key):
        locks = set(_task_locks(task))
        if locks and used_locks.intersection(locks):
            continue
        selected.append(task)
        used_locks.update(locks)
    return selected or [sorted(tasks, key=_task_sort_key)[0]]


def _database_first_candidates(
    ready_candidates: list[dict[str, Any]],
    tasks_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """只要数据库任务尚未成功完成，就禁止同批派发后端或前端任务。"""

    database_tasks = [
        task for task in tasks_by_id.values() if str(task.get("owner") or "") == "database"
    ]
    if not database_tasks:
        return ready_candidates
    database_done = all(
        task.get("status", RUNNABLE_STATUS) in {"completed", "already_satisfied"}
        for task in database_tasks
    )
    if database_done:
        return ready_candidates
    return [
        task
        for task in ready_candidates
        if str(task.get("owner") or "") == "database"
    ]


def _task_locks(task: dict[str, Any]) -> list[str]:
    """从 lock_scope、change_scope、target_files 和 allowed_paths 推导写锁。"""

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
    locks.extend(_string_list(task.get("target_files")))
    locks.extend(_string_list(task.get("allowed_paths")))
    return sorted({lock for lock in locks if lock})


def _string_list(value: Any) -> list[str]:
    """将列表输入规整为去空字符串列表。"""

    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _task_sort_key(task: dict[str, Any]) -> tuple[int, str]:
    """为调度选择提供稳定排序，数据库、后端、前端任务依次执行。"""

    owner_priority = {"database": 0, "backend": 1, "frontend": 2}
    return (owner_priority.get(str(task.get("owner")), 9), str(task.get("id")))


def _protocol_failure(task: dict[str, Any], reason: str) -> dict[str, Any]:
    """把 runner 协议异常包装为标准失败结果。"""

    display_reason = f"任务执行器返回结果不符合协议：{reason}"
    result = {
        "task_id": task["id"],
        "owner": task.get("owner"),
        "status": "failed",
        "failure_category": "runner_protocol_error",
        "failure_reason": display_reason,
        "agent_note": display_reason,
        "changed_files": [],
        "commands": [],
        "change_request": None,
    }
    result["scheduler_decision"] = classify_task_result(result)
    return result


def _normalized_scope(scope: dict[str, Any] | None) -> dict[str, str]:
    """规整构建范围，缺省时执行已准备的整应用任务。"""

    value = scope if isinstance(scope, dict) else {}
    target_type = str(value.get("type") or "application").strip()
    target_id = str(value.get("targetId") or value.get("target_id") or "").strip()
    api_contract_id = str(
        value.get("apiContractId") or value.get("api_contract_id") or ""
    ).strip()
    if target_type not in {"application", "page", "data_source", "endpoint"}:
        target_type = "application"
    if target_type == "application":
        target_id = target_id or "application"
    return {
        "type": target_type,
        "targetId": target_id,
        **(
            {"apiContractId": api_contract_id}
            if target_type == "endpoint" and api_contract_id
            else {}
        ),
    }


def _target_unit_ids(scope: dict[str, str]) -> list[str]:
    """将外部 scope 映射为目标 Unit ID。"""

    if scope["type"] == "page" and scope.get("targetId"):
        return [f"page:{scope['targetId']}"]
    if scope["type"] == "data_source" and scope.get("targetId"):
        return [f"database:{scope['targetId']}"]
    if scope["type"] == "endpoint" and scope.get("targetId"):
        api_contract_id = str(scope.get("apiContractId") or scope.get("api_contract_id") or "").strip()
        return [f"backend:endpoint:{api_contract_id}:{scope['targetId']}"] if api_contract_id else []
    return ["application:root"]


def _execution_unit_ids(
    build_task_plan: dict[str, Any],
    scope: dict[str, str],
) -> list[str]:
    """按 Unit Graph 解析目标 Unit 及其直接/传递前置 Unit。"""

    unit_graph = build_task_plan.get("unit_graph")
    unit_ids = _string_list(
        (unit_graph if isinstance(unit_graph, dict) else {}).get("nodes")
    )
    if scope["type"] == "application":
        return unit_ids

    available = set(unit_ids)
    selected: list[str] = []
    dependency_map = _unit_dependency_map(unit_graph)
    stack = [unit_id for unit_id in _target_unit_ids(scope) if unit_id in available]
    while stack:
        unit_id = stack.pop(0)
        if unit_id in selected:
            continue
        selected.append(unit_id)
        for dependency_unit_id in dependency_map.get(unit_id, []):
            if dependency_unit_id in available and dependency_unit_id not in selected:
                stack.append(dependency_unit_id)
    return selected


def _unit_dependency_map(unit_graph: Any) -> dict[str, list[str]]:
    """读取 Unit Graph depends_on 边，用于切片前置依赖闭包。"""

    graph = unit_graph if isinstance(unit_graph, dict) else {}
    result: dict[str, list[str]] = {}
    for edge in graph.get("edges", []) if isinstance(graph.get("edges"), list) else []:
        if not isinstance(edge, dict) or edge.get("type") != "depends_on":
            continue
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        if source and target:
            result.setdefault(target, []).append(source)
    return {unit_id: _dedupe_strings(dependencies) for unit_id, dependencies in result.items()}


def _dedupe_strings(values: list[str]) -> list[str]:
    """按首次出现顺序去重字符串。"""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
