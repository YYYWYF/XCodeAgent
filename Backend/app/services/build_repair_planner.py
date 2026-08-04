from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
import json
from fnmatch import fnmatch
from typing import Any

from app.services.engineering_acceptance import compile_repair_engineering_acceptance
from app.services.build_task_planner import (
    replace_build_task_plan_tasks,
    tasks_from_build_task_plan,
)


MAX_REPAIR_DEPTH = 1
RepairPlanner = Callable[[dict[str, Any]], dict[str, Any]]


def create_build_failure_repair_plan(
    *,
    failed_results: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    existing_repair_tasks: list[dict[str, Any]] | None = None,
    workspace_snapshot: dict[str, Any] | None = None,
    targeted_snapshot: dict[str, Any] | None = None,
    repair_planner: RepairPlanner | None = None,
) -> dict[str, Any]:
    """为可修复构建失败生成受调度器边界约束的修复计划。"""

    tasks_by_id = {str(task.get("id")): task for task in tasks if task.get("id")}
    existing_keys = {
        _repair_key(_repair_source_ref(task))
        for task in existing_repair_tasks or []
        if isinstance(task, dict)
    }
    repair_tasks: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    planner_inputs: list[dict[str, Any]] = []
    planner_outputs: list[dict[str, Any]] = []
    confirmations: list[dict[str, Any]] = []
    terminal_failures: list[dict[str, Any]] = []

    for result in failed_results:
        decision = result.get("scheduler_decision") or {}
        if decision.get("action") != "repair":
            continue

        parent_id = str(result.get("task_id") or "")
        parent_task = tasks_by_id.get(parent_id)
        if not parent_task:
            skipped.append({"task_id": parent_id, "reason": "missing_parent_task"})
            continue

        depth = _repair_depth(parent_task)
        if depth >= MAX_REPAIR_DEPTH:
            skipped.append({"task_id": parent_id, "reason": "repair_depth_exhausted"})
            continue

        source_ref = _source_ref(parent_task, result, depth=depth + 1)
        key = _repair_key(source_ref)
        if key in existing_keys:
            skipped.append({"task_id": parent_id, "reason": "repair_already_exists"})
            continue

        repair_input = create_repair_planner_input(
            original_task=parent_task,
            failed_attempt=result,
            workspace_snapshot=workspace_snapshot,
            targeted_snapshot=targeted_snapshot,
            source_ref=source_ref,
        )
        planner_inputs.append(repair_input)
        try:
            raw_plan = (
                repair_planner(repair_input)
                if repair_planner
                else _deterministic_repair_plan(repair_input)
            )
        except Exception as exc:  # pragma: no cover - defensive runtime boundary
            raw_plan = {
                "decision": "terminal_failure",
                "reason": f"RepairPlanner invocation failed: {exc}",
                "strategy": "",
                "boundaries": {},
                "repair_tasks": [],
                "failure_handling": "stop_build",
            }
        normalized = normalize_repair_plan(
            repair_input=repair_input,
            raw_plan=raw_plan,
            parent_task=parent_task,
            result=result,
            source_ref=source_ref,
        )
        planner_outputs.append(normalized)

        if normalized["decision"] == "requires_user_confirmation":
            confirmations.append(normalized)
            continue
        if normalized["decision"] == "terminal_failure":
            terminal_failures.append(normalized)
            continue

        for task in normalized.get("tasks", []):
            repair_tasks.append(task)
            existing_keys.add(key)

    decision_text = _overall_repair_decision(
        repair_tasks=repair_tasks,
        confirmations=confirmations,
        terminal_failures=terminal_failures,
    )
    status = {
        "repair": "ready",
        "requires_user_confirmation": "requires_user_confirmation",
        "terminal_failure": "terminal_failure",
    }.get(decision_text, "not_required")
    requested_paths = sorted(
        {
            path
            for confirmation in confirmations
            for path in _string_list(
                confirmation.get("requestedPaths")
                or confirmation.get("boundaries", {}).get("allowed_paths")
            )
        }
    )
    requested_resources = _deduplicated_requested_resources(
        [
            resource
            for confirmation in confirmations
            for resource in confirmation.get("requestedResources", [])
            if isinstance(resource, dict)
        ]
    )
    plan_id = _stable_repair_plan_id(
        [item.get("source_ref", {}) for item in planner_inputs],
        requested_paths,
    )
    return {
        "version": "0.1.0",
        "status": status,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "build_scheduler",
        "decision": decision_text,
        "planId": plan_id,
        "requestedPaths": requested_paths,
        "requestedResources": requested_resources,
        "tasks": repair_tasks if decision_text == "repair" else [],
        "repair_tasks": repair_tasks if decision_text == "repair" else [],
        "requires_user_confirmation": confirmations,
        "terminal_failures": terminal_failures,
        "skipped": skipped,
        "planner_inputs": planner_inputs,
        "planner_outputs": planner_outputs,
        "summary": {
            "total": len(repair_tasks) if decision_text == "repair" else 0,
            "frontend": len([task for task in repair_tasks if task["owner"] == "frontend"]),
            "backend": len([task for task in repair_tasks if task["owner"] == "backend"]),
            "database": len([task for task in repair_tasks if task["owner"] == "database"]),
            "requires_user_confirmation": len(confirmations),
            "terminal_failure": len(terminal_failures),
        },
        "prepared_by": {
            "agent": "build-repair-planner",
            "mode": "deep_agent_constrained" if repair_planner else "deterministic",
            "source": "build_scheduler_failure_classification",
        },
    }


def create_repair_planner_input(
    *,
    original_task: dict[str, Any],
    failed_attempt: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None = None,
    targeted_snapshot: dict[str, Any] | None = None,
    source_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    acceptance = _string_list(original_task.get("acceptance_criteria"))
    change_scope = original_task.get("change_scope", [])
    return {
        "version": "0.1.0",
        "original_task": original_task,
        "failed_attempt": failed_attempt,
        "change_scope": {
            "items": change_scope if isinstance(change_scope, list) else [],
            "allowed_paths": _string_list(original_task.get("allowed_paths")),
            "target_files": _string_list(original_task.get("target_files")),
            "policy": "Repair tasks must not expand the original task change_scope or allowed_paths.",
        },
        "workspace_snapshot": targeted_snapshot or workspace_snapshot or {},
        "failure_logs_refs": _failure_log_refs(failed_attempt),
        "acceptance_criteria": acceptance,
        "constraints": {
            "max_repair_depth": MAX_REPAIR_DEPTH,
            "current_repair_depth": _repair_depth(original_task),
            "must_not_modify_scheduler_state": True,
            "must_not_change_confirmed_project_plan": True,
            "must_not_expand_contracts_without_confirmation": True,
        },
        "source_ref": source_ref or {},
        "scheduler_decision": failed_attempt.get("scheduler_decision", {}),
    }


def normalize_repair_plan(
    *,
    repair_input: dict[str, Any],
    raw_plan: dict[str, Any] | None,
    parent_task: dict[str, Any],
    result: dict[str, Any],
    source_ref: dict[str, Any],
) -> dict[str, Any]:
    """校验 RepairPlanner 输出并投射稳定路径及业务资源扩展请求。"""

    plan = raw_plan if isinstance(raw_plan, dict) else {}
    decision = str(plan.get("decision") or "").strip()
    if decision not in {"repair", "requires_user_confirmation", "terminal_failure"}:
        decision = "terminal_failure"

    normalized = {
        "decision": decision,
        "strategy": str(plan.get("strategy") or ""),
        "boundaries": _repair_boundaries(parent_task, plan.get("boundaries")),
        "failure_handling": str(plan.get("failure_handling") or ""),
        "reason": str(plan.get("reason") or ""),
        "raw_plan": plan,
        "repair_input_ref": {
            "task_id": repair_input.get("source_ref", {}).get("parent_task_id"),
            "failure_signature": repair_input.get("source_ref", {}).get(
                "failure_signature"
            ),
        },
        "tasks": [],
    }
    normalized["requestedPaths"] = _requested_repair_paths(
        parent_task,
        plan.get("boundaries"),
    )
    normalized["requestedResources"] = _requested_repair_resources(
        plan.get("boundaries")
    )
    normalized["planId"] = _stable_repair_plan_id(
        [normalized["repair_input_ref"]],
        normalized["requestedPaths"],
    )
    if decision != "repair":
        return normalized

    raw_tasks = plan.get("repair_tasks") or plan.get("tasks") or [{}]
    if not isinstance(raw_tasks, list):
        raw_tasks = [{}]
    tasks: list[dict[str, Any]] = []
    for index, raw_task in enumerate(raw_tasks):
        task = _repair_task(
            parent_task,
            result,
            source_ref,
            raw_task=raw_task if isinstance(raw_task, dict) else {},
            strategy=normalized["strategy"],
            boundaries=normalized["boundaries"],
        )
        if len(raw_tasks) > 1:
            task = {
                **task,
                "id": f"{task['id']}:{index + 1}",
                "task_id": f"{task['task_id']}:{index + 1}",
            }
        tasks.append(task)
    return {**normalized, "tasks": tasks}


def approve_repair_scope_confirmation(
    repair_task_plan: dict[str, Any],
) -> dict[str, Any]:
    """将已获用户批准的范围确认计划编译为原任务授权内的修复任务。"""

    inputs = [
        item
        for item in repair_task_plan.get("planner_inputs", [])
        if isinstance(item, dict)
    ]
    inputs_by_task = {
        str(item.get("source_ref", {}).get("parent_task_id") or ""): item
        for item in inputs
    }
    tasks: list[dict[str, Any]] = []
    for confirmation in repair_task_plan.get("requires_user_confirmation", []):
        if not isinstance(confirmation, dict):
            continue
        task_id = str(confirmation.get("repair_input_ref", {}).get("task_id") or "")
        repair_input = inputs_by_task.get(task_id)
        if not repair_input:
            continue
        parent_task = repair_input.get("original_task")
        failed_attempt = repair_input.get("failed_attempt")
        source_ref = repair_input.get("source_ref")
        if not all(isinstance(item, dict) for item in (parent_task, failed_attempt, source_ref)):
            continue
        approved = normalize_repair_plan(
            repair_input=repair_input,
            raw_plan={
                "decision": "repair",
                "strategy": confirmation.get("strategy") or "Apply the user-approved bounded repair.",
                "boundaries": confirmation.get("boundaries", {}),
                "repair_tasks": [{}],
                "failure_handling": "append_repair_task_and_resume_scheduler",
            },
            parent_task=parent_task,
            result=failed_attempt,
            source_ref=source_ref,
        )
        tasks.extend(approved.get("tasks", []))
    return {
        **repair_task_plan,
        "status": "ready" if tasks else "terminal_failure",
        "decision": "repair" if tasks else "terminal_failure",
        "tasks": tasks,
        "repair_tasks": tasks,
        "approvedPlanId": repair_task_plan.get("planId"),
    }


def _requested_repair_paths(
    parent_task: dict[str, Any],
    raw_boundaries: Any,
) -> list[str]:
    """读取 Planner 请求路径；未显式提供时退回原任务精确授权范围。"""

    boundaries = raw_boundaries if isinstance(raw_boundaries, dict) else {}
    requested = _string_list(
        boundaries.get("requestedPaths")
        or boundaries.get("requested_paths")
        or boundaries.get("allowed_paths")
    )
    return requested or _string_list(parent_task.get("allowed_paths"))


def _requested_repair_resources(raw_boundaries: Any) -> list[dict[str, str]]:
    """只接受 RepairPlanner 明确列出的稳定业务资源，不从文件路径猜测。"""

    boundaries = raw_boundaries if isinstance(raw_boundaries, dict) else {}
    value = boundaries.get("requested_resources") or boundaries.get("requestedResources")
    return _deduplicated_requested_resources(
        [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
    )


def _deduplicated_requested_resources(
    resources: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """校验并去重修复扩展资源，应用级扩展必须由独立应用计划处理。"""

    result: dict[str, dict[str, str]] = {}
    for resource in resources:
        resource_type = str(resource.get("type") or "").strip()
        target_id = str(resource.get("targetId") or resource.get("target_id") or "").strip()
        if resource_type not in {"page", "endpoint", "api_contract", "data_source"} or not target_id:
            continue
        result.setdefault(
            f"{resource_type}:{target_id}",
            {"type": resource_type, "targetId": target_id},
        )
    return list(result.values())


def _stable_repair_plan_id(parts: Any, requested_paths: list[str]) -> str:
    """根据失败签名和请求路径生成可跨恢复请求复用的计划 ID。"""

    payload = json.dumps(
        {"parts": parts, "requestedPaths": requested_paths},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def append_repair_tasks_to_build_plan(
    *,
    build_task_plan: dict[str, Any],
    repair_task_plan: dict[str, Any],
) -> dict[str, Any]:
    """把修复任务追加回全局 Build DAG，保留原任务所属 Unit。"""

    repair_tasks = [
        task
        for task in repair_task_plan.get("tasks", [])
        if isinstance(task, dict) and task.get("id")
    ]
    if not repair_tasks:
        return build_task_plan

    existing_tasks = tasks_from_build_task_plan(build_task_plan)
    existing_ids = {str(task["id"]) for task in existing_tasks}
    next_tasks = [
        *existing_tasks,
        *[task for task in repair_tasks if str(task["id"]) not in existing_ids],
    ]
    summary = {
        **(build_task_plan.get("summary") if isinstance(build_task_plan.get("summary"), dict) else {}),
        "total": len(next_tasks),
        "pending": len([task for task in next_tasks if task.get("status", "pending") == "pending"]),
        "running": len([task for task in next_tasks if task.get("status") == "running"]),
        "completed": len([task for task in next_tasks if task.get("status") == "completed"]),
        "failed": len([task for task in next_tasks if task.get("status") == "failed"]),
        "repair": len([task for task in next_tasks if _is_repair_task(task)]),
    }
    return {
        **replace_build_task_plan_tasks(build_task_plan, next_tasks),
        "summary": summary,
        "repair_task_plan": repair_task_plan,
    }


def close_repaired_parent_tasks(
    *,
    tasks: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    successful_repair_status_by_parent = {
        _parent_task_id(task): str(task.get("status"))
        for task in tasks
        if _is_repair_task(task)
        and task.get("status") in {"completed", "already_satisfied"}
        and _has_successful_result(task, results)
        and _parent_task_id(task)
    }
    if not successful_repair_status_by_parent:
        return tasks

    now = datetime.now(UTC).isoformat()
    return [
        {
            **task,
            "status": successful_repair_status_by_parent[str(task.get("id"))],
            "completed_by_repair": True,
            "repair_closed_at": now,
        }
        if str(task.get("id")) in successful_repair_status_by_parent
        and task.get("status") == "failed"
        else task
        for task in tasks
    ]


def _repair_task(
    parent_task: dict[str, Any],
    result: dict[str, Any],
    source_ref: dict[str, Any],
    *,
    raw_task: dict[str, Any] | None = None,
    strategy: str = "",
    boundaries: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """基于失败父任务创建受限修复任务，并继承 Unit 与来源引用。"""

    raw_task = raw_task or {}
    parent_id = str(parent_task["id"])
    repair_id = f"repair:{parent_id}:{source_ref['failure_signature'][:12]}"
    description = (
        str(raw_task.get("description") or "").strip()
        or f"修复任务 {parent_id} 的执行失败："
        f"{result.get('agent_note') or result.get('failure_category') or '实现未通过验证'}"
    )
    repair_change_scope = _repair_change_scope(
        parent_task,
        raw_task,
        strategy=strategy,
    )
    # 修复任务仅继承父任务的结果型工程契约，文件差异必须按本轮修复范围重新编译。
    repair_task = {
        "id": repair_id,
        "kind": "repair",
        "owner": parent_task.get("owner"),
        "task_type": parent_task.get("task_type"),
        "unit_id": parent_task.get("unit_id", "application:root"),
        "title": str(raw_task.get("title") or "").strip()
        or f"修复 {parent_task.get('title') or parent_id}",
        "description": description,
        "dependencies": [],
        "status": "pending",
        "source_refs": {
            "parent": (
                parent_task.get("source_refs")
                if isinstance(parent_task.get("source_refs"), dict)
                else {}
            ),
            "repair": source_ref,
        },
        "repairs": {
            "task_id": parent_id,
            "result_task_id": result.get("task_id"),
        },
        "allowed_paths": _string_list(parent_task.get("allowed_paths")),
        "target_files": _string_list(parent_task.get("target_files")),
        "change_scope": repair_change_scope,
        "database_scope": parent_task.get("database_scope", {}),
        "approval": parent_task.get("approval", {}),
        "impact_scope": parent_task.get("impact_scope", {}),
        "can_run_in_parallel": False,
        "parallel_reason": "repair task must serialize with the failed parent scope.",
        "repair_strategy": strategy,
        "repair_boundaries": boundaries or _repair_boundaries(parent_task, {}),
        "acceptance_criteria": [],
        "acceptance_checks": [],
        "engineering_context": parent_task.get("engineering_context", {}),
        "failure_evidence": {
            "failure_category": result.get("failure_category"),
            "failure_signature": source_ref["failure_signature"],
            "agent_note": result.get("agent_note"),
            "changed_files": result.get("changed_files", []),
            "commands": result.get("commands", []),
        },
    }
    return compile_repair_engineering_acceptance(repair_task, parent_task)


def _repair_change_scope(
    parent_task: dict[str, Any],
    raw_task: dict[str, Any],
    *,
    strategy: str,
) -> list[dict[str, str]]:
    """提取 RepairPlanner 的精确修复范围，并拒绝越过父任务授权的路径。"""

    allowed_paths = _string_list(parent_task.get("allowed_paths"))
    parent_paths = [
        str(item.get("path") or "").strip()
        for item in parent_task.get("change_scope", [])
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    ]
    raw_scope = raw_task.get("change_scope")
    result: list[dict[str, str]] = []
    if isinstance(raw_scope, list):
        for item in raw_scope:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path or not _repair_path_allowed(path, allowed_paths, parent_paths):
                continue
            operation = str(item.get("operation") or "modify").lower()
            if operation not in {"add", "modify", "delete"}:
                operation = "modify"
            result.append(
                {
                    "operation": operation,
                    "path": path,
                    "description": str(item.get("description") or "执行受限修复。"),
                }
            )
    if result:
        return result

    # 兼容旧 RepairPlanner：只在描述明确提到某个父任务精确路径时推导 modify。
    repair_text = "\n".join(
        str(value or "")
        for value in (
            raw_task.get("title"),
            raw_task.get("description"),
            strategy,
        )
    )
    return [
        {
            "operation": "modify",
            "path": path,
            "description": "根据修复描述修改该精确目标文件。",
        }
        for path in parent_paths
        if path.lstrip("./") in repair_text or path in repair_text
    ]


def _repair_path_allowed(
    path: str,
    allowed_paths: list[str],
    parent_paths: list[str],
) -> bool:
    """判断 RepairPlanner 声明路径是否仍位于父任务文件授权范围。"""

    normalized = path.lstrip("./")
    patterns = [*allowed_paths, *parent_paths]
    for pattern in patterns:
        candidate = pattern.lstrip("./")
        if candidate.endswith("/**") and normalized.startswith(candidate[:-3].rstrip("/") + "/"):
            return True
        if fnmatch(normalized, candidate):
            return True
    return False


def _source_ref(
    parent_task: dict[str, Any],
    result: dict[str, Any],
    *,
    depth: int,
) -> dict[str, Any]:
    signature = str(
        result.get("failure_signature")
        or result.get("failure_category")
        or result.get("agent_note")
        or result.get("task_id")
        or "unknown_failure"
    )
    return {
        "type": "build_task_failure",
        "parent_task_id": parent_task["id"],
        "failed_task_id": result.get("task_id"),
        "failure_category": result.get("failure_category"),
        "failure_signature": signature,
        "repair_depth": depth,
    }


def _repair_key(source_ref: Any) -> tuple[str, str]:
    source = source_ref if isinstance(source_ref, dict) else {}
    return (
        str(source.get("parent_task_id") or source.get("failed_task_id") or ""),
        str(source.get("failure_signature") or ""),
    )


def _repair_source_ref(task: dict[str, Any]) -> dict[str, Any]:
    """读取 repair 任务附带的内部修复来源，避免污染通用来源引用。"""

    source_refs = task.get("source_refs")
    if not isinstance(source_refs, dict):
        return {}
    repair = source_refs.get("repair")
    return repair if isinstance(repair, dict) else {}


def _repair_depth(task: dict[str, Any]) -> int:
    source_ref = _repair_source_ref(task)
    try:
        return int(source_ref.get("repair_depth") or 0)
    except (TypeError, ValueError):
        return 0


def _is_repair_task(task: dict[str, Any]) -> bool:
    return task.get("kind") == "repair" or (
        _repair_source_ref(task).get("type") == "build_task_failure"
    )


def _parent_task_id(task: dict[str, Any]) -> str:
    repairs = task.get("repairs") if isinstance(task.get("repairs"), dict) else {}
    source_ref = _repair_source_ref(task)
    return str(repairs.get("task_id") or source_ref.get("parent_task_id") or "")


def _has_successful_result(task: dict[str, Any], results: list[dict[str, Any]]) -> bool:
    """确认修复任务存在 completed 或 already_satisfied 的成功结果。"""

    task_id = str(task.get("id") or "")
    return any(
        result.get("task_id") == task_id
        and result.get("status") in {"completed", "already_satisfied"}
        for result in results
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _deterministic_repair_plan(repair_input: dict[str, Any]) -> dict[str, Any]:
    result = repair_input.get("failed_attempt") if isinstance(repair_input, dict) else {}
    original_task = repair_input.get("original_task") if isinstance(repair_input, dict) else {}
    task_id = original_task.get("id") if isinstance(original_task, dict) else ""
    return {
        "decision": "repair",
        "strategy": (
            "Create a bounded repair task that fixes the failed implementation "
            "inside the original task scope, then rerun the original acceptance criteria."
        ),
        "boundaries": {
            "change_scope_policy": "stay_within_original_task_change_scope",
            "allowed_paths_policy": "stay_within_original_task_allowed_paths",
            "contract_policy": "do_not_change_confirmed_contracts_without_user_confirmation",
        },
        "repair_tasks": [
            {
                "title": f"修复 {task_id}",
                "description": (
                    "根据失败结果修复实现："
                    f"{result.get('agent_note') or result.get('failure_category') or '实现未通过验证'}"
                )
                if isinstance(result, dict)
                else "根据失败结果修复实现。",
            }
        ],
        "failure_handling": "append_repair_task_and_resume_scheduler",
    }


def _repair_boundaries(
    parent_task: dict[str, Any],
    raw_boundaries: Any,
) -> dict[str, Any]:
    raw = raw_boundaries if isinstance(raw_boundaries, dict) else {}
    return {
        **raw,
        "allowed_paths": _string_list(parent_task.get("allowed_paths")),
        "change_scope": parent_task.get("change_scope", []),
        "target_files": _string_list(parent_task.get("target_files")),
        "scope_policy": "must_not_expand_original_task_scope",
        "scheduler_policy": "planner_may_only_return_plan_not_mutate_state",
    }


def _failure_log_refs(result: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("failure_logs_refs", "failure_log_refs", "logs_ref", "log_ref"):
        value = result.get(key)
        if isinstance(value, list):
            refs.extend(str(item) for item in value if str(item).strip())
        elif value:
            refs.append(str(value))
    commands = result.get("commands")
    if isinstance(commands, list):
        for command in commands:
            if isinstance(command, dict) and command.get("log_ref"):
                refs.append(str(command["log_ref"]))
    return refs


def _overall_repair_decision(
    *,
    repair_tasks: list[dict[str, Any]],
    confirmations: list[dict[str, Any]],
    terminal_failures: list[dict[str, Any]],
) -> str:
    if confirmations:
        return "requires_user_confirmation"
    if terminal_failures:
        return "terminal_failure"
    if repair_tasks:
        return "repair"
    return "terminal_failure"
