from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.state import ProjectState
from app.persistence.checkpoints import (
    workflow_checkpoint_db_path,
    workflow_checkpointer,
)


def route_workflow_start(state: ProjectState) -> str:
    if state.get("resume_from") == "requirements":
        return "requirements"
    if state.get("resume_from") == "project_planning":
        return "project_planning"
    if state.get("resume_from") == "detail_confirmation":
        return "detail_confirmation"
    if state.get("resume_from") == "inspect_workspace":
        return "inspect_workspace"
    if state.get("resume_from") == "prepare_build_tasks":
        return "prepare_build_tasks"
    if state.get("resume_from") == "build":
        return "build"
    if state.get("resume_from") == "integration_test":
        return "integration_test"
    if state.get("resume_from") == "launch_project":
        return "launch_project"
    if state.get("resume_from") == "acceptance":
        return "acceptance"
    if state.get("resume_from") == "finalize_project":
        return "finalize_project"
    return "classify_request_complexity"


def route_request_complexity(state: ProjectState) -> str:
    if state["request_complexity"] == "simple" and state.get("editor_mode") in {
        "frontend",
        "backend",
    }:
        return "direct_modification"
    return "requirements"


def route_test_validation(state: ProjectState) -> str:
    if state.get("quality_gate_passed"):
        return "launch_project"
    next_action = state.get("integration_next_action")
    if next_action == "repair_build":
        return "build"
    if next_action == "await_user_input":
        return "await_user_input"
    return "handle_failure"


def route_requirements(state: ProjectState) -> str:
    clarification = state.get("clarification", {})
    if (
        isinstance(clarification, dict)
        and clarification.get("status") == "requires_user_input"
    ):
        return "await_user_input"
    return "project_planning"


def route_project_planning(state: ProjectState) -> str:
    return (
        "await_user_input"
        if state.get("status") == "requires_user_input"
        else "detail_confirmation"
    )


def route_detail_confirmation(state: ProjectState) -> str:
    return (
        "await_user_input"
        if state.get("status") == "requires_user_input"
        else "inspect_workspace"
    )


def route_prepare_build_tasks(state: ProjectState) -> str:
    return (
        "await_user_input" if state.get("status") == "requires_user_input" else "build"
    )


def build_graph(*, checkpointer):
    builder = StateGraph(ProjectState)

    builder.add_node("classify_request_complexity", nodes.classify_request_complexity)
    builder.add_node("requirements", nodes.requirements)
    builder.add_node("direct_modification", nodes.direct_modification)
    builder.add_node("project_planning", nodes.project_planning)
    builder.add_node("detail_confirmation", nodes.detail_confirmation)
    builder.add_node("inspect_workspace", nodes.inspect_workspace)
    builder.add_node("prepare_build_tasks", nodes.prepare_build_tasks)
    builder.add_node("build", nodes.build)
    builder.add_node("integration_test", nodes.integration_test)
    builder.add_node("launch_project", nodes.launch_project)
    builder.add_node("acceptance", nodes.acceptance)
    builder.add_node("finalize_project", nodes.finalize_project)
    builder.add_node("handle_failure", nodes.handle_failure)

    builder.add_conditional_edges(
        START,
        route_workflow_start,
        {
            "classify_request_complexity": "classify_request_complexity",
            "requirements": "requirements",
            "project_planning": "project_planning",
            "detail_confirmation": "detail_confirmation",
            "inspect_workspace": "inspect_workspace",
            "prepare_build_tasks": "prepare_build_tasks",
            "build": "build",
            "integration_test": "integration_test",
            "launch_project": "launch_project",
            "acceptance": "acceptance",
            "finalize_project": "finalize_project",
        },
    )
    builder.add_conditional_edges(
        "classify_request_complexity",
        route_request_complexity,
        {
            "requirements": "requirements",
            "direct_modification": "direct_modification",
        },
    )
    builder.add_conditional_edges(
        "requirements",
        route_requirements,
        {
            "project_planning": "project_planning",
            "await_user_input": END,
        },
    )
    builder.add_conditional_edges(
        "project_planning",
        route_project_planning,
        {
            "detail_confirmation": "detail_confirmation",
            "await_user_input": END,
        },
    )
    builder.add_conditional_edges(
        "detail_confirmation",
        route_detail_confirmation,
        {
            "inspect_workspace": "inspect_workspace",
            "await_user_input": END,
        },
    )
    builder.add_edge("inspect_workspace", "prepare_build_tasks")
    builder.add_conditional_edges(
        "prepare_build_tasks",
        route_prepare_build_tasks,
        {
            "build": "build",
            "await_user_input": END,
        },
    )
    builder.add_edge("build", "integration_test")
    builder.add_edge("direct_modification", "integration_test")
    builder.add_conditional_edges(
        "integration_test",
        route_test_validation,
        {
            "launch_project": "launch_project",
            "build": "build",
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_edge("launch_project", END)
    builder.add_edge("acceptance", "finalize_project")
    builder.add_edge("finalize_project", END)
    builder.add_edge("handle_failure", END)

    return builder.compile(checkpointer=checkpointer)


_WORKFLOW_GRAPHS = {}


async def workflow_graph_for_request(
    *,
    workspace: str | None = None,
    project_id: str | None = None,
):
    db_path = workflow_checkpoint_db_path(workspace=workspace, project_id=project_id)
    cache_key = str(db_path)
    if cache_key not in _WORKFLOW_GRAPHS:
        _WORKFLOW_GRAPHS[cache_key] = build_graph(
            checkpointer=await workflow_checkpointer(
                workspace=workspace,
                project_id=project_id,
            )
        )
    return _WORKFLOW_GRAPHS[cache_key]


def clear_workflow_graph_cache() -> None:
    _WORKFLOW_GRAPHS.clear()
