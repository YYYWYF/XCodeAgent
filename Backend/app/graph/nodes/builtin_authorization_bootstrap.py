"""Builtin 独立权限 Schema 与 Seed 初始化节点，不执行模板 Java Bootstrap。"""

from app.graph.nodes.authorization_bootstrap import _build_plan_gate_error
from app.graph.nodes.common import workspace_from_state
from app.graph.state import ProjectState
from app.services.builtin_authorization_orchestrator import ensure_builtin_authorization_task, workflow_task_result


def builtin_authorization_bootstrap(state: ProjectState) -> dict:
    """先复核确认 DAG，业务失败保留真实结果后按原策略放行 Build。"""

    workspace = workspace_from_state(state) or ""
    gate = _build_plan_gate_error(workspace, state)
    result = ({"status": "failed", "failure_category": "build_plan_invalid", "message": gate}
              if gate else workflow_task_result(ensure_builtin_authorization_task(workspace, source="build")))
    return {"phase": "builtin_authorization_bootstrap", "status": "failed" if gate else "completed",
            "builtin_authorization_bootstrap_result": result, "authorization_bootstrap_result": result,
            "message": result["message"], "error": gate,
            "timeline": ["builtin_authorization_bootstrap"]}
