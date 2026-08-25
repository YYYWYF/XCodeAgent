from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.state import ProjectState
from app.persistence.checkpoints import (
    workflow_checkpoint_db_path,
    workflow_checkpointer,
)


def route_workflow_start(state: ProjectState) -> str:
    """让主 Workflow 从开发就绪门禁、实体绑定或指定恢复节点开始。"""

    if state.get("resume_from") == "entity_source_binding":
        return "entity_source_binding"
    if state.get("resume_from") == "development_readiness_gate":
        return "development_readiness_gate"
    if state.get("resume_from") == "project_planning":
        return "project_planning"
    if state.get("resume_from") == "inspect_workspace":
        return "inspect_workspace"
    if state.get("resume_from") == "inspect_database_context":
        return "prepare_build_tasks"
    if state.get("resume_from") == "prepare_build_tasks":
        return "prepare_build_tasks"
    if state.get("resume_from") == "build":
        return "build"
    if state.get("resume_from") == "test_phase_confirmation":
        return "test_phase_confirmation"
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
    if str(state.get("selected_entity_id") or "").strip():
        return "entity_source_binding"
    return "development_readiness_gate"


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
    if target == "inspect_database_context":
        return "prepare_build_tasks"
    if target in {
        "integration_test",
        "entity_source_binding",
        "project_planning",
        "inspect_workspace",
        "prepare_build_tasks",
        "build",
    }:
        return target
    return "integration_test"


def route_build_result(state: ProjectState) -> str:
    """仅允许完整成功的构建进入测试阶段确认门，失败走失败处理。"""

    build_summary = state.get("build_summary")
    summary_status = (
        str(build_summary.get("status") or "")
        if isinstance(build_summary, dict)
        else ""
    )
    if state.get("status") == "failed":
        return "handle_failure"
    if summary_status == "completed":
        return "test_phase_confirmation"
    if summary_status == "requires_confirmation" or state.get("status") == "requires_user_input":
        return "await_user_input"
    return "handle_failure"


def route_test_phase_confirmation(state: ProjectState) -> str:
    """根据测试阶段确认结果选择暂停或继续集成测试。"""

    if state.get("status") == "requires_user_input":
        return "await_user_input"
    build_summary = state.get("build_summary")
    build_completed = (
        isinstance(build_summary, dict)
        and build_summary.get("status") == "completed"
    )
    if state.get("status") == "completed" and build_completed:
        return "integration_test"
    return "handle_failure"


def route_entity_source_binding(state: ProjectState) -> str:
    """实体数据源绑定始终作为独立交互结束，不自动进入页面/API开发。"""

    return "await_user_input"


def route_development_readiness(state: ProjectState) -> str:
    """开发就绪时进入工作区检查，否则停下等待用户手动完成实体绑定。"""

    return "await_user_input" if state.get("status") == "requires_user_input" else "inspect_workspace"


def route_project_planning(state: ProjectState) -> str:
    """技术计划调整确认后重新执行开发就绪门禁，失败则统一处理。"""

    if state.get("status") == "requires_user_input":
        return "await_user_input"
    if state.get("status") == "failed":
        return "handle_failure"
    return "development_readiness_gate"


def route_prepare_build_tasks(state: ProjectState) -> str:
    """任务 DAG 生成失败进入统一失败处理，成功才进入 Build 调度。"""

    if state.get("status") == "requires_user_input":
        return "await_user_input"
    if state.get("status") == "failed":
        return "handle_failure"
    return "build"


def route_acceptance(state: ProjectState) -> str:
    """只有结构化验收通过动作才能进入最终完成节点。"""

    return "finalize_project" if state.get("accepted") is True else "await_user_input"


def build_graph(*, checkpointer):
    """构建从开发就绪检查开始的主应用开发图。"""

    builder = StateGraph(ProjectState)

    builder.add_node("development_readiness_gate", nodes.development_readiness_gate)
    builder.add_node("entity_source_binding", nodes.entity_source_binding)
    builder.add_node("project_planning", nodes.project_planning)
    builder.add_node("inspect_workspace", nodes.inspect_workspace)
    builder.add_node("prepare_build_tasks", nodes.prepare_build_tasks)
    builder.add_node("build", nodes.build)
    builder.add_node("test_phase_confirmation", nodes.test_phase_confirmation)
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
            "development_readiness_gate": "development_readiness_gate",
            "entity_source_binding": "entity_source_binding",
            "project_planning": "project_planning",
            "inspect_workspace": "inspect_workspace",
            "prepare_build_tasks": "prepare_build_tasks",
            "build": "build",
            "test_phase_confirmation": "test_phase_confirmation",
            "integration_test": "integration_test",
            "small_task_repair": "small_task_repair",
            "launch_project": "launch_project",
            "acceptance": "acceptance",
            "finalize_project": "finalize_project",
        },
    )
    builder.add_conditional_edges(
        "development_readiness_gate",
        route_development_readiness,
        {
            "inspect_workspace": "inspect_workspace",
            "await_user_input": END,
        },
    )
    builder.add_conditional_edges(
        "entity_source_binding",
        route_entity_source_binding,
        {"await_user_input": END},
    )
    builder.add_conditional_edges(
        "project_planning",
        route_project_planning,
        {
            "development_readiness_gate": "development_readiness_gate",
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_edge("inspect_workspace", "prepare_build_tasks")
    builder.add_conditional_edges(
        "prepare_build_tasks",
        route_prepare_build_tasks,
        {
            "build": "build",
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_conditional_edges(
        "build",
        route_build_result,
        {
            "test_phase_confirmation": "test_phase_confirmation",
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_conditional_edges(
        "test_phase_confirmation",
        route_test_phase_confirmation,
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
            "entity_source_binding": "entity_source_binding",
            "project_planning": "project_planning",
            "inspect_workspace": "inspect_workspace",
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
