from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.state import ProjectState
from app.persistence.checkpoints import workflow_checkpoint_db_path, workflow_checkpointer
from app.services.application_planning_persistence import persist_confirmed_application_plan


def _route_start(state: ProjectState) -> str:
    """根据独立创建规划会话的恢复点选择两节点入口。"""

    resume_from = state.get("resume_from")
    return resume_from if resume_from == "project_planning" else "requirements"


def _route_requirements(state: ProjectState) -> str:
    """需求未确认时结束当前轮次，否则进入项目规划。"""

    clarification = state.get("clarification")
    return "await_user_input" if isinstance(clarification, dict) and clarification.get("status") == "requires_user_input" else "project_planning"


def _project_planning(state: ProjectState) -> dict:
    """复用项目规划节点，并在用户确认后写回 application.json。"""

    update = nodes.project_planning(state)
    if update.get("status") != "completed":
        return {**update, "workflow_scope": "application_planning"}
    merged_state = {**state, **update}
    return {
        **update,
        "workflow_scope": "application_planning",
        "application_planning_confirmation": persist_confirmed_application_plan(merged_state),
    }


def build_application_planning_graph(*, checkpointer):
    """构建确认项目规划后即结束的创建规划两节点 Graph。"""

    builder = StateGraph(ProjectState)
    builder.add_node("requirements", nodes.requirements)
    builder.add_node("project_planning", _project_planning)
    builder.add_conditional_edges(START, _route_start, {
        "requirements": "requirements",
        "project_planning": "project_planning",
    })
    builder.add_conditional_edges("requirements", _route_requirements, {
        "project_planning": "project_planning",
        "await_user_input": END,
    })
    builder.add_edge("project_planning", END)
    return builder.compile(checkpointer=checkpointer)


_APPLICATION_PLANNING_GRAPHS: dict[str, object] = {}


async def application_planning_graph_for_request(*, workspace: str | None = None, project_id: str | None = None):
    """按工作区复用独立创建规划 Graph 与 SQLite checkpointer。"""

    db_path = workflow_checkpoint_db_path(workspace=workspace, project_id=project_id)
    cache_key = str(db_path)
    if cache_key not in _APPLICATION_PLANNING_GRAPHS:
        _APPLICATION_PLANNING_GRAPHS[cache_key] = build_application_planning_graph(
            checkpointer=await workflow_checkpointer(workspace=workspace, project_id=project_id)
        )
    return _APPLICATION_PLANNING_GRAPHS[cache_key]


def clear_application_planning_graph_cache() -> None:
    """清理创建规划 Graph 缓存，供应用退出时释放资源。"""

    _APPLICATION_PLANNING_GRAPHS.clear()
