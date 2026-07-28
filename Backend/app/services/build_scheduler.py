from __future__ import annotations

from collections import defaultdict
from fnmatch import fnmatch
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TERMINAL_STATUSES = {"completed", "failed", "already_satisfied"}
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


def verify_task_file_changes(
    *,
    results: list[dict[str, Any]],
    code_change_set: dict[str, Any] | None,
    tasks: list[dict[str, Any]] | None = None,
    workspace_root: str | None = None,
) -> list[dict[str, Any]]:
    """验证 agent 是否实际写入了文件。

    如果 code_change_set 为 None 或无文件变更，将 "completed" 结果改为
    "failed"（failure_category="no_file_changes"），避免幻影完成。
    例外情况：结构化 already_satisfied 结果必须同时通过精确目标路径、文件状态和
    全量验收证据校验，不能依赖自然语言短语。
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

    tasks_by_id = {
        str(task.get("id") or task.get("task_id")): task
        for task in tasks or []
        if task.get("id") or task.get("task_id")
    }
    verified: list[dict[str, Any]] = []
    for result in results:
        verified_result = dict(result)
        status = verified_result.get("status")
        if status not in {"completed", "already_satisfied"}:
            verified.append(verified_result)
            continue

        task = tasks_by_id.get(str(verified_result.get("task_id") or ""), {})
        authorized_paths = _task_authorized_paths(task)
        attributed_paths = [
            path for path in changed_paths if _path_matches_any(path, authorized_paths)
        ]
        if not attributed_paths and status == "already_satisfied":
            evidence_error = _already_satisfied_evidence_error(
                task,
                verified_result.get("satisfaction_evidence"),
                workspace_root=workspace_root,
            )
            if evidence_error is None:
                verified_result["failure_category"] = None
                verified_result["scheduler_decision"] = classify_task_result(verified_result)
            else:
                _mark_no_file_changes_failure(
                    verified_result,
                    detail=f"already_satisfied evidence rejected: {evidence_error}",
                )
        elif not attributed_paths:
            _mark_no_file_changes_failure(verified_result)
        else:
            verified_result["status"] = "completed"
            verified_result["changed_files"] = attributed_paths
            verified_result["scheduler_decision"] = classify_task_result(verified_result)
        verified.append(verified_result)
    return verified


def _mark_no_file_changes_failure(
    result: dict[str, Any],
    *,
    detail: str | None = None,
) -> None:
    """把缺少实际文件变化或无效满足证据的结果原地转换为可修复失败。"""

    result["status"] = "failed"
    result["failure_category"] = "no_file_changes"
    result["failure_reason"] = (
        "Agent 报告任务已完成，但未在工作区产生文件变更；"
        "已满足声明必须提供精确目标文件与全部验收点证据。"
    )
    original_note = str(result.get("agent_note") or "")
    suffix = "VERIFICATION FAILED: Agent reported completion but no authorized files changed."
    if detail:
        suffix = f"{suffix} {detail}"
    result["agent_note"] = f"{original_note}\n\n{suffix}" if original_note else suffix
    result["scheduler_decision"] = classify_task_result(result)


def _already_satisfied_evidence_error(
    task: dict[str, Any],
    evidence: Any,
    *,
    workspace_root: str | None,
) -> str | None:
    """确定性校验已满足声明中的精确目标、磁盘状态和逐条验收证据。"""

    if not isinstance(evidence, dict):
        return "missing satisfaction_evidence"
    target_files = [
        str(path).lstrip("./")
        for path in task.get("targetFiles", [])
        if str(path).strip()
    ]
    raw_reported_files = evidence.get("target_files")
    if not isinstance(raw_reported_files, list):
        return "satisfaction_evidence.target_files must be a list"
    reported_files = {
        str(path).lstrip("./")
        for path in raw_reported_files
        if str(path).strip()
    }
    if not target_files or any(path not in reported_files for path in target_files):
        return "target_files do not cover every exact task target"
    path_error = _target_state_error(task, target_files, workspace_root=workspace_root)
    if path_error:
        return path_error

    criteria = [
        str(item)
        for item in task.get("acceptance_criteria") or task.get("acceptanceCriteria") or []
        if str(item).strip()
    ]
    raw_criteria = evidence.get("acceptance_criteria")
    reports = (
        {
            str(item.get("criterion")): item
            for item in raw_criteria
            if isinstance(item, dict) and item.get("criterion")
        }
        if isinstance(raw_criteria, list)
        else {}
    )
    for criterion in criteria:
        report = reports.get(criterion)
        if (
            not report
            or report.get("status") != "passed"
            or not str(report.get("evidence") or "").strip()
        ):
            return f"missing passed evidence for acceptance criterion: {criterion}"
    return None


def _target_state_error(
    task: dict[str, Any],
    target_files: list[str],
    *,
    workspace_root: str | None,
) -> str | None:
    """核对目标文件当前状态是否符合 add、modify 或 delete 操作。"""

    if not workspace_root:
        return "workspace root is unavailable"
    root = Path(workspace_root).expanduser().resolve()
    operations = {
        str(change.get("path") or "").lstrip("./"): str(change.get("operation") or "modify")
        for change in task.get("change_scope", [])
        if isinstance(change, dict) and change.get("path")
    }
    for target in target_files:
        if any(token in target for token in ("*", "?", "[")):
            return f"target path is not exact: {target}"
        resolved = (root / target).resolve()
        if resolved != root and root not in resolved.parents:
            return f"target escapes workspace: {target}"
        operation = operations.get(target, "modify")
        if operation == "delete" and resolved.exists():
            return f"deleted target still exists: {target}"
        if operation != "delete" and not resolved.is_file():
            return f"required target file does not exist: {target}"
    return None


def _task_authorized_paths(task: dict[str, Any]) -> list[str]:
    """汇总单个任务声明的精确或通配授权路径。"""

    paths = [str(path) for path in task.get("allowed_paths", []) if str(path).strip()]
    paths.extend(
        str(change.get("path"))
        for change in task.get("change_scope", [])
        if isinstance(change, dict) and change.get("path")
    )
    paths.extend(str(path) for path in task.get("targetFiles", []) if str(path).strip())
    return list(dict.fromkeys(path.lstrip("./") for path in paths if path))


def _path_matches_any(path: str, patterns: list[str]) -> bool:
    """判断实际变更路径是否落在任务授权范围内。"""

    normalized = path.lstrip("./")
    for pattern in patterns:
        normalized_pattern = pattern.lstrip("./")
        if normalized_pattern.endswith("/**"):
            if normalized.startswith(normalized_pattern[:-3].rstrip("/") + "/"):
                return True
        elif fnmatch(normalized, normalized_pattern):
            return True
    return False


def summarize_build_runtime(
    tasks: list[dict[str, Any]],
    build_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """汇总当前执行切片内任务的运行状态。"""

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
        "completed": counts["completed"] + counts.get("already_satisfied", 0),
        "failed": counts["failed"],
        "pending": counts["pending"],
        "running": counts["running"],
        "already_satisfied": counts.get("already_satisfied", 0),
        "results": len(build_results),
        "repairable_failures": len(repairable),
        "requires_confirmation": len(confirmation),
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
    """读取任务依赖 ID，兼容 dependencies 与 dependsOn。"""

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
    """检查当前可见任务集中是否存在缺失依赖。"""

    return [
        f"Task {task.get('id')} depends on missing task {dependency}."
        for task in tasks
        for dependency in _dependencies(task)
        if dependency not in tasks_by_id
    ]


def _lock_compatible_batch(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从就绪任务中选择文件锁不冲突的一批任务。"""

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
    """从 lock_scope、change_scope、targetFiles 和 allowed_paths 推导写锁。"""

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
    """将列表输入规整为去空字符串列表。"""

    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _task_sort_key(task: dict[str, Any]) -> tuple[int, str]:
    """为调度选择提供稳定排序，数据源任务优先于前端任务。"""

    owner_priority = {"data_source": 0, "frontend": 1}
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
    if target_type not in {"application", "page", "data_source", "endpoint"}:
        target_type = "application"
    if target_type == "application":
        target_id = target_id or "application"
    return {"type": target_type, "targetId": target_id}


def _target_unit_ids(scope: dict[str, str]) -> list[str]:
    """将外部 scope 映射为目标 Unit ID。"""

    if scope["type"] == "page" and scope.get("targetId"):
        return [f"page:{scope['targetId']}"]
    if scope["type"] == "data_source" and scope.get("targetId"):
        return [f"data-source:{scope['targetId']}"]
    if scope["type"] == "endpoint" and scope.get("targetId"):
        api_contract_id = str(scope.get("apiContractId") or scope.get("api_contract_id") or "").strip()
        return [f"endpoint:{api_contract_id}:{scope['targetId']}"] if api_contract_id else []
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
