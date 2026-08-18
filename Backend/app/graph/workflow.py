from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.nodes.database_context import (
    _build_task_plan_for_context,
    _workspace_snapshot_from_state,
)
from app.graph.nodes.tasks import (
    _build_execution_scope_from_state,
    _latest_compact_project_plan,
    _resolve_build_context,
)
from app.graph.state import ProjectState
from app.persistence.checkpoints import (
    workflow_checkpoint_db_path,
    workflow_checkpointer,
)
from app.services.database_planning_context import database_context_requirement
from app.services.frontend_scaffold import scaffold_frontend_pages


def route_workflow_start(state: ProjectState) -> str:
    """让主 Workflow 从页面细节确认或其后的恢复节点开始执行。"""

    if state.get("resume_from") == "detail_confirmation":
        return "detail_confirmation"
    if state.get("resume_from") == "project_planning":
        return "project_planning"
    if state.get("resume_from") == "inspect_workspace":
        return "inspect_workspace"
    if state.get("resume_from") == "inspect_database_context":
        return "inspect_database_context"
    if state.get("resume_from") == "prepare_build_tasks":
        return "prepare_build_tasks"
    if state.get("resume_from") == "build":
        return "build"
    if state.get("resume_from") == "integration_test":
        return "integration_test"
    if state.get("resume_from") == "small_task_repair":
        return "small_task_repair"
    if state.get("resume_from") == "launch_project":
        return "launch_project"
    if state.get("resume_from") == "acceptance":
        return "acceptance"
    if state.get("resume_from") == "finalize_project":
        return "finalize_project"
    return "detail_confirmation"


def route_test_validation(state: ProjectState) -> str:
    """根据质量门禁和小任务结果选择后续节点，修复不再回到 build。"""

    next_action = state.get("integration_next_action")
    # 用户确认门必须优先于上一轮遗留的质量门结果，否则重试测试时会越过确认直接启动预览。
    if state.get("status") == "requires_user_input" or next_action == "await_user_input":
        return "await_user_input"
    if state.get("quality_gate_passed"):
        return "launch_project"
    if next_action == "small_task_repair":
        return "small_task_repair"
    return "handle_failure"


def route_small_task_result(state: ProjectState) -> str:
    """根据 SmallTask Agent 的完成、升级或失败结果选择主图路由。"""

    if state.get("status") == "requires_user_input":
        return "await_user_input"
    if state.get("status") == "failed":
        return "handle_failure"
    target = str(state.get("small_task_route") or "integration_test")
    if target in {
        "integration_test",
        "detail_confirmation",
        "project_planning",
        "inspect_workspace",
        "inspect_database_context",
        "prepare_build_tasks",
        "build",
    }:
        return target
    return "integration_test"


def route_build_result(state: ProjectState) -> str:
    """仅允许完整成功的构建进入测试，确认与失败走各自终态。"""

    build_summary = state.get("build_summary")
    summary_status = (
        str(build_summary.get("status") or "")
        if isinstance(build_summary, dict)
        else ""
    )
    if summary_status == "completed":
        return "integration_test"
    if summary_status == "requires_confirmation" or state.get("status") == "requires_user_input":
        return "await_user_input"
    return "handle_failure"


def route_detail_confirmation(state: ProjectState) -> str:
    """细节确认完成后进入工作区检查，等待用户输入时停止。"""

    return (
        "await_user_input"
        if state.get("status") == "requires_user_input"
        else "inspect_workspace"
    )


def route_project_planning(state: ProjectState) -> str:
    """项目计划调整确认后重新进入页面细节确认，失败则停止在失败处理。"""

    if state.get("status") == "requires_user_input":
        return "await_user_input"
    if state.get("status") == "failed":
        return "handle_failure"
    return "detail_confirmation"


def route_workspace_inspection(state: ProjectState) -> str:
    """按已确认接口 data_origin 决定是否进入数据库上下文检查节点。"""

    try:
        project_plan = _latest_compact_project_plan(state)
        workspace_snapshot = _workspace_snapshot_from_state(state)
        build_task_plan = _build_task_plan_for_context(
            state,
            project_plan,
            workspace_snapshot,
        )
        build_context = _resolve_build_context(
            state,
            project_plan,
            _build_execution_scope_from_state(state),
            build_task_plan,
        )
        requirement = database_context_requirement(project_plan, build_context)
    except Exception:
        return "prepare_build_tasks"
    return (
        "inspect_database_context"
        if requirement.get("required") or requirement.get("status") == "blocked"
        else "prepare_build_tasks"
    )


def route_database_context_inspection(state: ProjectState) -> str:
    """数据库上下文检查失败时等待用户处理，否则继续任务规划。"""

    return (
        "await_user_input"
        if state.get("status") == "requires_user_input"
        else "prepare_build_tasks"
    )


def route_prepare_build_tasks(state: ProjectState) -> str:
    """任务 DAG 生成需要用户输入时停止，否则进入 Build 调度。"""

    return (
        "await_user_input" if state.get("status") == "requires_user_input" else "build"
    )


def route_acceptance(state: ProjectState) -> str:
    """只有结构化验收通过动作才能进入最终完成节点。"""

    return "finalize_project" if state.get("accepted") is True else "await_user_input"


def build_graph(*, checkpointer):
    """构建从 ProjectPlan 页面细节确认开始的主应用开发图。"""

    builder = StateGraph(ProjectState)

    builder.add_node("detail_confirmation", nodes.detail_confirmation)
    builder.add_node("project_planning", nodes.project_planning)
    builder.add_node("inspect_workspace", nodes.inspect_workspace)
    builder.add_node("inspect_database_context", nodes.inspect_database_context)
    builder.add_node("prepare_build_tasks", nodes.prepare_build_tasks)
    builder.add_node("build", nodes.build)
    builder.add_node("integration_test", nodes.integration_test)
    builder.add_node("small_task_repair", nodes.small_task_repair)
    builder.add_node("launch_project", nodes.launch_project)
    builder.add_node("acceptance", nodes.acceptance)
    builder.add_node("finalize_project", nodes.finalize_project)
    builder.add_node("handle_failure", nodes.handle_failure)

    builder.add_conditional_edges(
        START,
        route_workflow_start,
        {
            "detail_confirmation": "detail_confirmation",
            "project_planning": "project_planning",
            "inspect_workspace": "inspect_workspace",
            "inspect_database_context": "inspect_database_context",
            "prepare_build_tasks": "prepare_build_tasks",
            "build": "build",
            "integration_test": "integration_test",
            "small_task_repair": "small_task_repair",
            "launch_project": "launch_project",
            "acceptance": "acceptance",
            "finalize_project": "finalize_project",
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
    builder.add_conditional_edges(
        "project_planning",
        route_project_planning,
        {
            "detail_confirmation": "detail_confirmation",
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_conditional_edges(
        "inspect_workspace",
        route_workspace_inspection,
        {
            "inspect_database_context": "inspect_database_context",
            "prepare_build_tasks": "prepare_build_tasks",
        },
    )
    builder.add_conditional_edges(
        "inspect_database_context",
        route_database_context_inspection,
        {
            "prepare_build_tasks": "prepare_build_tasks",
            "await_user_input": END,
        },
    )
    builder.add_conditional_edges(
        "prepare_build_tasks",
        route_prepare_build_tasks,
        {
            "build": "build",
            "await_user_input": END,
        },
    )
    builder.add_conditional_edges(
        "build",
        route_build_result,
        {
            "integration_test": "integration_test",
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_conditional_edges(
        "integration_test",
        route_test_validation,
        {
            "launch_project": "launch_project",
            "small_task_repair": "small_task_repair",
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_conditional_edges(
        "small_task_repair",
        route_small_task_result,
        {
            "integration_test": "integration_test",
            "detail_confirmation": "detail_confirmation",
            "project_planning": "project_planning",
            "inspect_workspace": "inspect_workspace",
            "inspect_database_context": "inspect_database_context",
            "prepare_build_tasks": "prepare_build_tasks",
            "build": "build",
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_edge("launch_project", END)
    builder.add_conditional_edges(
        "acceptance",
        route_acceptance,
        {
            "finalize_project": "finalize_project",
            "await_user_input": END,
        },
    )
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
