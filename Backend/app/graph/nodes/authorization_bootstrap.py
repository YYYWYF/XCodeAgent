"""Build 前由平台执行生成项目权限数据库初始化。"""

from __future__ import annotations

from pathlib import Path

from app.graph.nodes.common import workspace_from_state
from app.graph.state import ProjectState
from app.services.authorization_bootstrap import run_authorization_bootstrap
from app.workspace.task_documents import load_build_task_plan_json


def authorization_bootstrap(state: ProjectState) -> dict:
    """执行当前确认权限 manifest 的 Bootstrap，失败时阻断 Build。"""

    workspace = workspace_from_state(state) or ""
    gate_error = _build_plan_gate_error(workspace, state)
    result = (
        {
            "status": "failed",
            "failure_category": "build_plan_invalid",
            "message": gate_error,
        }
        if gate_error
        else run_authorization_bootstrap(
            workspace,
            state.get("technical_plan")
            if isinstance(state.get("technical_plan"), dict)
            else {},
        )
    )
    succeeded = result.get("status") in {"executed", "reused", "skipped"}
    return {
        "phase": "authorization_bootstrap",
        "status": "completed" if succeeded else "failed",
        "authorization_bootstrap_result": result,
        "message": str(result.get("message") or "权限数据库初始化完成。"),
        "error": "" if succeeded else str(result.get("message") or "权限数据库初始化失败。"),
        "timeline": ["authorization_bootstrap"],
    }


def _build_plan_gate_error(workspace: str, state: ProjectState) -> str:
    """在任何数据库副作用前复核当前工作区的已确认 Build DAG。"""

    if not workspace:
        return "缺少工作区，不能执行权限数据库初始化。"
    path = Path(workspace).expanduser() / ".xcodeagent" / "plans" / "build-task-plan.json"
    try:
        plan = load_build_task_plan_json(path)
    except (OSError, TypeError, ValueError):
        return "最新 Build DAG 无法读取，不能执行权限数据库初始化。"
    if plan.get("schema_version") != "build-dag.v3":
        return "最新 Build DAG 版本无效，不能执行权限数据库初始化。"
    if plan.get("status") != "ready" or plan.get("confirmation_status") != "confirmed":
        return "最新 Build DAG 尚未确认，不能执行权限数据库初始化。"
    validation = (
        plan.get("task_graph", {}).get("validation")
        if isinstance(plan.get("task_graph"), dict)
        else None
    )
    if not isinstance(validation, dict) or validation.get("is_valid") is not True:
        return "最新 Build DAG 未通过校验，不能执行权限数据库初始化。"
    scope = state.get("build_execution_scope")
    planned_scope = plan.get("build_execution_scope")
    if isinstance(scope, dict) and scope and isinstance(planned_scope, dict) and planned_scope != scope:
        return "最新 Build DAG 与当前 Build 范围不一致，不能执行权限数据库初始化。"
    return ""
