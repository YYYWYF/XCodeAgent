"""Build 节点重新开始时，只保留正式合同与当前工作区事实。"""

from copy import deepcopy
from typing import Any

from app.workspace.task_documents import _TASK_RUNTIME_FIELDS


def restart_build_state(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """清除上次 Build 的运行结果，并重新扫描当前磁盘作为本轮上下文。"""

    # 保留已确认的执行范围和业务合同，旧进度、修复计划和审批不能授权新一轮。
    restarted = {
        **state,
        "tasks": [], "ready_tasks": [], "pending_build_results": [],
        "build_task_plan": {}, "build_results": [], "build_summary": {},
        "build_run_id": "", "build_run_plan_path": "", "build_run_plan_sha256": "",
        "build_execution_slice": {}, "retry_failed_tasks": False,
        "repair_task_plan": {}, "repair_task_plan_path": "", "repair_tasks": [],
        "repair_iteration": 0, "database_change_plan": {}, "database_approval_requests": [],
        "platform_projection_evidence": {}, "route_projection_evidence": {},
        "clarification": {}, "request": "", "workspace_snapshot": {},
    }
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
