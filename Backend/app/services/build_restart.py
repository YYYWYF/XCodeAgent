"""Build 节点重新开始时，只保留正式合同与当前工作区事实。"""

from copy import deepcopy
from typing import Any

from app.services.business_acceptance_verifier import verify_business_acceptance
from app.services.engineering_acceptance_verifier import verify_engineering_acceptance
from app.workspace.task_documents import (
    _TASK_RUNTIME_FIELDS,
    build_run_task_plan_json_path,
    build_task_plan_sha256,
    load_build_task_plan_json,
)


_COMPLETED_STATUSES = {"completed", "already_satisfied"}


def confirmed_plan_build_handoff(
    state: dict[str, Any], plan: dict[str, Any], *, already_confirmed: bool = False,
) -> dict[str, Any]:
    """新确认计划切换 Build 运行绑定，同计划的重复确认保留原执行进度。"""
    if (already_confirmed and state.get("build_run_id") and state.get("build_run_plan_path")
            and state.get("build_run_plan_sha256") == build_task_plan_sha256(plan)):
        return {}
    # Graph State 按字段合并；仅更新任务计划会留下旧绑定，甚至被旧重试来源再次覆盖。
    # 只清除内存中的上一轮执行信息，已有源码和落盘执行证据仍由原机制读取与保留。
    return {
        "build_run_id": "", "build_run_plan_path": "", "build_run_plan_sha256": "",
        "build_results": [], "build_summary": {}, "build_execution_slice": {},
        "ready_tasks": [], "pending_build_results": [],
        "retry_failed_tasks": False, "retry_source_run_id": "",
        # 上轮点击过的确认是一次性动作，不能替新计划自动跨过下游确认门。
        "test_phase_confirmation": {}, "review_phase_confirmation": {},
        "acceptance_phase_confirmation": {}, "code_review_repair_confirmation": {},
    }


def restart_build_state_fields() -> dict[str, Any]:
    """重生成时撤销旧执行绑定和衍生证据，保留源码与用户选定的目标。"""
    return {
        "tasks": [], "ready_tasks": [], "pending_build_results": [],
        "build_task_plan": {}, "build_results": [], "build_summary": {},
        "build_run_id": "", "build_run_plan_path": "", "build_run_plan_sha256": "",
        "build_execution_slice": {}, "retry_failed_tasks": False,
        "repair_task_plan": {}, "repair_task_plan_path": "", "repair_tasks": [],
        "repair_iteration": 0, "database_change_plan": {}, "database_approval_requests": [],
        "platform_projection_evidence": {}, "route_projection_evidence": {},
        "clarification": {}, "workspace_snapshot": {},
        "workspace_revision": "", "workspace_snapshot_path": "",
        "retry_source_run_id": "",
    }


def restart_build_state(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """清除上次 Build 的运行结果，并重新扫描当前磁盘作为本轮上下文。"""
    restarted = {**state, **restart_build_state_fields()}
    return refresh_build_workspace(restarted)


def refresh_build_workspace(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """重试和重启都扫描当前磁盘，保留调用方选定的任务运行态。"""

    from app.graph.nodes.workspace_inspection import inspect_workspace

    scanned = inspect_workspace(state)
    scan_fields = {key: value for key, value in scanned.items() if key.startswith("workspace_")}
    return {**state, "workspace_snapshot": {}, **scan_fields}, scan_fields


def fresh_build_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从正式计划的任务合同重建 pending 清单，不继承过去的完成事实。"""

    runtime_fields = _TASK_RUNTIME_FIELDS | {
        "retry_count", "completed_by_repair", "repair_closed_at", "approved_database_change_plan",
    }
    return [
        {**{key: deepcopy(value) for key, value in task.items() if key not in runtime_fields},
         "status": "pending"}
        for task in tasks
    ]


def resume_endpoint_build_tasks(
    state: dict[str, Any], plan: dict[str, Any], tasks: list[dict[str, Any]], *,
    workspace_root: str, restarting: bool,
) -> list[dict[str, Any]]:
    """在同一 Endpoint 计划重跑时，保留当前仍可核实的任务产物。"""

    # 普通失败重试已经绑定入口快照；显式重跑还可从最近一次同计划的入口补回被重置的完成记录。
    recorded = plan.get("task_registry") or {}
    if not isinstance(recorded, dict):
        recorded = {}
    previous = _previous_build_registry(state, plan) if restarting else {}
    candidates = fresh_build_tasks(tasks) if restarting else tasks
    resumed: list[dict[str, Any]] = []
    completed: set[str] = set()
    for original, candidate in zip(tasks, candidates, strict=True):
        task_id = str(original["id"])
        status = original.get("status")
        failure = original.get("failure_detail")
        decision = failure.get("scheduler_decision") if isinstance(failure, dict) else None
        report_invalid = isinstance(decision, dict) and decision.get("reason") == "invalid_structured_response"
        if status in _COMPLETED_STATUSES:
            proof = original if restarting else None
        elif status == "pending" or report_invalid:
            proof = recorded.get(task_id, {})
            if not isinstance(proof, dict) or proof.get("status") not in _COMPLETED_STATUSES:
                proof = previous.get(task_id)
        else:
            proof = None

        # 不从旧结果跨过数据库任务或其他无法用文件核实的任务。
        if not isinstance(proof, dict) or proof.get("status") not in _COMPLETED_STATUSES:
            resumed.append(candidate)
            if candidate.get("status") in _COMPLETED_STATUSES:
                completed.add(task_id)
            continue
        if (original.get("owner") == "database" or not original.get("change_scope")
                or any(dep not in completed for dep in (original.get("dependencies") or []))):
            resumed.append(candidate)
            continue
        _, errors = verify_engineering_acceptance(
            task=original, status="already_satisfied", code_change_set=None, workspace_root=workspace_root,
        )
        if errors or (report_invalid and not _recheck_invalid_report(state, original, workspace_root)):
            resumed.append(candidate)
            continue
        resumed.append({
            **{key: value for key, value in candidate.items() if key not in _TASK_RUNTIME_FIELDS},
            **{key: deepcopy(proof[key]) for key in _TASK_RUNTIME_FIELDS if key in proof},
        })
        completed.add(task_id)
    return resumed


def _previous_build_registry(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """仅接受当前调度器指向、且规划摘要相同的只读入口副本。"""

    update = plan.get("last_update")
    if (not isinstance(update, dict) or update.get("stage") != "build_scheduler"
            or update.get("updated_by") != "build-scheduler"):
        return {}
    try:
        previous = load_build_task_plan_json(build_run_task_plan_json_path(state, update.get("build_run_id", "")))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(previous, dict) or build_task_plan_sha256(previous) != build_task_plan_sha256(plan):
        return {}
    return previous.get("task_registry", {})


def _recheck_invalid_report(state: dict[str, Any], task: dict[str, Any], workspace_root: str) -> bool:
    """格式错误的任务只有重新通过非空业务检查才可复用，避免忽略真实执行失败。"""

    if not task.get("business_acceptance_checks"):
        return False
    verified = verify_business_acceptance(task, workspace_root, formal_artifacts=state.get("project_plan"))
    return verified["status"] == "passed"
