from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.subgraphs import acceptance_subgraph
from app.graph.state import ProjectState
from app.services.authorization_bootstrap import authorization_bootstrap_enabled
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
    if state.get("resume_from") == "application_revision":
        return "application_revision"
    if state.get("resume_from") == "technical_planning":
        return "technical_planning"
    if state.get("resume_from") == "project_planning":
        return "project_planning"
    if state.get("resume_from") == "inspect_workspace":
        return "inspect_workspace"
    if state.get("resume_from") == "inspect_database_context":
        return "prepare_build_tasks"
    if state.get("resume_from") == "prepare_build_tasks":
        return "prepare_build_tasks"
    if state.get("resume_from") == "build":
        return (
            "authorization_bootstrap"
            if authorization_bootstrap_enabled(state.get("technical_plan"))
            else "build"
        )
    if state.get("resume_from") == "authorization_bootstrap":
        return "authorization_bootstrap"
    if state.get("resume_from") == "unit_test":
        return "unit_test"
    if state.get("resume_from") == "unit_test_repair":
        return "unit_test_repair"
    if state.get("resume_from") == "test_phase_confirmation":
        return "test_phase_confirmation"
    if state.get("resume_from") == "review_phase_confirmation":
        return "review_phase_confirmation"
    if state.get("resume_from") == "code_review":
        return "code_review"
    if state.get("resume_from") == "acceptance_phase_confirmation":
        return "acceptance_phase_confirmation"
    if state.get("resume_from") == "integration_test":
        return "integration_test"
    if state.get("resume_from") == "small_task_repair":
        return "small_task_repair"
    if state.get("resume_from") == "acceptance":
        return "acceptance"
    if state.get("resume_from") == "finalize_project":
        return "finalize_project"
    if str(state.get("selected_entity_id") or "").strip():
        return "entity_source_binding"
    return "development_readiness_gate"


def route_application_revision(state: ProjectState) -> str:
    """正式产物收口后按 lifecycle 构建范围选择目标门禁或工作区检查。"""

    if state.get("status") != "revision_artifacts_confirmed":
        return "await_user_input"
    scope = state.get("build_execution_scope")
    scope_type = str(scope.get("type") or "") if isinstance(scope, dict) else ""
    if scope_type == "application":
        return "inspect_workspace"
    if scope_type in {"page", "endpoint"}:
        return "development_readiness_gate"
    raise ValueError("正式修订已确认，但 lifecycle 构建范围缺失或不合法。")


def route_test_validation(state: ProjectState) -> str:
    """根据质量门禁和小任务结果选择后续节点，修复不再回到 build。"""

    next_action = state.get("integration_next_action")
    # 用户确认门必须优先于上一轮遗留的质量门结果，否则重试测试时会越过确认直接启动预览。
    if state.get("status") == "requires_user_input" or next_action == "await_user_input":
        return "await_user_input"
    if state.get("quality_gate_passed"):
        return "review_phase_confirmation"
    if next_action == "small_task_repair":
        return "small_task_repair"
    return "handle_failure"


def route_unit_test_result(state: ProjectState) -> str:
    """根据开发阶段单元测试门禁结果选择确认、修复或失败路径。"""

    if state.get("status") == "requires_user_input":
        return "await_user_input"
    next_action = str(state.get("unit_test_next_action") or "")
    if next_action == "unit_test_repair":
        return "unit_test_repair"
    if next_action == "test_phase_confirmation" and state.get("unit_test_gate_passed") is True:
        return "test_phase_confirmation"
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
        "unit_test",
        "project_planning",
        "inspect_workspace",
        "prepare_build_tasks",
        "build",
    }:
        return target
    return "integration_test"


def route_build_result(state: ProjectState) -> str:
    """仅允许完整成功的构建进入开发阶段单元测试门禁，失败走失败处理。"""

    build_summary = state.get("build_summary")
    summary_status = (
        str(build_summary.get("status") or "")
        if isinstance(build_summary, dict)
        else ""
    )
    if state.get("status") == "failed":
        return "handle_failure"
    if summary_status == "completed":
        return "unit_test"
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
    if (
        state.get("status") == "completed"
        and build_completed
        and state.get("unit_test_gate_passed") is True
    ):
        return "integration_test"
    return "handle_failure"


def route_review_phase_confirmation(state: ProjectState) -> str:
    """根据审查阶段确认结果选择暂停或开始代码审查。"""

    if state.get("status") == "requires_user_input":
        return "await_user_input"
    if state.get("status") == "completed" and state.get("quality_gate_passed") is True:
        return "code_review"
    return "handle_failure"


def route_code_review(state: ProjectState) -> str:
    """审查子图暂停时等待用户，完成后进入验收阶段确认，异常进入失败处理。"""

    if state.get("status") == "requires_user_input":
        return "await_user_input"
    if state.get("status") == "completed":
        return "acceptance_phase_confirmation"
    return "handle_failure"


def route_acceptance_phase_confirmation(state: ProjectState) -> str:
    """根据用户确认结果选择进入验收子图或暂停等待。"""

    if state.get("status") == "requires_user_input":
        return "await_user_input"
    if state.get("status") == "completed":
        return "acceptance"
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
    return (
        "authorization_bootstrap"
        if authorization_bootstrap_enabled(state.get("technical_plan"))
        else "build"
    )


def route_authorization_bootstrap(state: ProjectState) -> str:
    """权限 Bootstrap 成功后才允许继续 Build。"""

    return "build" if state.get("status") == "completed" else "handle_failure"


def route_acceptance(state: ProjectState) -> str:
    """只有结构化验收通过动作才能进入最终完成节点。"""

    return "finalize_project" if state.get("accepted") is True else "await_user_input"


def build_graph(*, checkpointer):
    """构建从开发就绪检查开始的主应用开发图。"""

    builder = StateGraph(ProjectState)

    builder.add_node("development_readiness_gate", nodes.development_readiness_gate)
    builder.add_node("application_revision", nodes.start_application_revision)
    # TechnicalPlan 二次修改使用同一实现节点，但以真实 technical_planning
    # 入口暴露给运行时，避免把用户确认后的规划请求显示成未知的 revision 流程。
    builder.add_node("technical_planning", nodes.start_application_revision)
    builder.add_node("entity_source_binding", nodes.entity_source_binding)
    builder.add_node("project_planning", nodes.project_planning)
    builder.add_node("inspect_workspace", nodes.inspect_workspace)
    builder.add_node("prepare_build_tasks", nodes.prepare_build_tasks)
    builder.add_node("authorization_bootstrap", nodes.authorization_bootstrap)
    builder.add_node("build", nodes.build)
    builder.add_node("unit_test", nodes.unit_test)
    builder.add_node("unit_test_repair", nodes.unit_test_repair)
    builder.add_node("test_phase_confirmation", nodes.test_phase_confirmation)
    builder.add_node("review_phase_confirmation", nodes.review_phase_confirmation)
    builder.add_node("code_review", nodes.code_review)
    builder.add_node("acceptance_phase_confirmation", nodes.acceptance_phase_confirmation)
    builder.add_node("integration_test", nodes.integration_test)
    builder.add_node("small_task_repair", nodes.small_task_repair)
    # 验收必须作为真实子图挂载，协议层才能在项目启动期间逐条收到 custom 进度，
    # 不能再由同步包装节点 invoke，否则子步骤会在启动完成后才一次性交付。
    builder.add_node("acceptance", acceptance_subgraph)
    builder.add_node("finalize_project", nodes.finalize_project)
    builder.add_node("handle_failure", nodes.handle_failure)

    builder.add_conditional_edges(
        START,
        route_workflow_start,
        {
            "development_readiness_gate": "development_readiness_gate",
            "application_revision": "application_revision",
            "technical_planning": "technical_planning",
            "entity_source_binding": "entity_source_binding",
            "project_planning": "project_planning",
            "inspect_workspace": "inspect_workspace",
            "prepare_build_tasks": "prepare_build_tasks",
            "authorization_bootstrap": "authorization_bootstrap",
            "build": "build",
            "unit_test": "unit_test",
            "unit_test_repair": "unit_test_repair",
            "test_phase_confirmation": "test_phase_confirmation",
            "review_phase_confirmation": "review_phase_confirmation",
            "code_review": "code_review",
            "acceptance_phase_confirmation": "acceptance_phase_confirmation",
            "integration_test": "integration_test",
            "small_task_repair": "small_task_repair",
            "acceptance": "acceptance",
            "finalize_project": "finalize_project",
        },
    )
    builder.add_conditional_edges(
        "application_revision",
        route_application_revision,
        {
            "development_readiness_gate": "development_readiness_gate",
            "inspect_workspace": "inspect_workspace",
            "await_user_input": END,
        },
    )
    builder.add_conditional_edges(
        "technical_planning",
        route_application_revision,
        {
            "development_readiness_gate": "development_readiness_gate",
            "inspect_workspace": "inspect_workspace",
            "await_user_input": END,
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
            "authorization_bootstrap": "authorization_bootstrap",
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_conditional_edges(
        "authorization_bootstrap",
        route_authorization_bootstrap,
        {"build": "build", "handle_failure": "handle_failure"},
    )
    builder.add_conditional_edges(
        "build",
        route_build_result,
        {
            "unit_test": "unit_test",
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_conditional_edges(
        "unit_test",
        route_unit_test_result,
        {
            "unit_test_repair": "unit_test_repair",
            "test_phase_confirmation": "test_phase_confirmation",
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_conditional_edges(
        "unit_test_repair",
        route_small_task_result,
        {
            "unit_test": "unit_test",
            "integration_test": "integration_test",
            "project_planning": "project_planning",
            "inspect_workspace": "inspect_workspace",
            "prepare_build_tasks": "prepare_build_tasks",
            "build": "build",
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
        "review_phase_confirmation",
        route_review_phase_confirmation,
        {
            "code_review": "code_review",
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_conditional_edges(
        "code_review",
        route_code_review,
        {
            "acceptance_phase_confirmation": "acceptance_phase_confirmation",
            # 扫描发现问题时，子图必须在当前审查 thread 暂停，等待结构化 repair_all。
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_conditional_edges(
        "acceptance_phase_confirmation",
        route_acceptance_phase_confirmation,
        {
            "acceptance": "acceptance",
            "await_user_input": END,
            "handle_failure": "handle_failure",
        },
    )
    builder.add_conditional_edges(
        "integration_test",
        route_test_validation,
        {
            "review_phase_confirmation": "review_phase_confirmation",
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
    checkpointer = await workflow_checkpointer(workspace=workspace, project_id=project_id)
    cached = _WORKFLOW_GRAPHS.get(cache_key)
    if cached is None or cached[0] is not checkpointer:
        cached = (
            checkpointer,
            build_graph(checkpointer=checkpointer),
        )
        _WORKFLOW_GRAPHS[cache_key] = cached
    return cached[1]


def clear_workflow_graph_cache(*, cache_key: str | None = None) -> None:
    """清理全部或单个 checkpoint 数据库对应的主 Workflow Graph 缓存。"""

    if cache_key is None:
        _WORKFLOW_GRAPHS.clear()
    else:
        _WORKFLOW_GRAPHS.pop(cache_key, None)
