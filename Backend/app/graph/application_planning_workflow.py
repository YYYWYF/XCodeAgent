from __future__ import annotations

import asyncio

from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.application_planning_revision import (
    DESIGN_CHANGE_TARGET_NODES,
    analyze_design_intent,
    cleared_design_change_context,
    design_artifact_node_state,
    design_chat_response,
    design_node_update,
    design_node_was_applied,
    is_design_change,
    prepare_ui_revision_state,
    route_design_intent,
)
from app.graph.state import ProjectState
from app.domain.application_lifecycle import (
    ApplicationLifecycleError,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    utc_now,
)
from app.persistence.checkpoints import workflow_checkpoint_db_path, workflow_checkpointer
from app.services.application_planning_persistence import confirm_application_planning_artifacts
from app.services.application_lifecycle import (
    application_lifecycle_payload,
    ensure_application_lifecycle,
    load_application_lifecycle,
    persist_application_lifecycle_transition,
)


def _route_start(state: ProjectState) -> str:
    """根据原创建规划 thread 的恢复点选择正常阶段或设计意图入口。"""

    resume_from = state.get("resume_from")
    if resume_from == "design_intent_analysis":
        return "design_intent_analysis"
    if resume_from in {"product_planning", "ui_confirmation", "technical_planning"}:
        return resume_from
    return "requirements"


def _route_requirements(state: ProjectState) -> str:
    """需求未确认时结束当前轮次，否则进入产品规划。"""

    requirement_spec = state.get("requirement_spec")
    if (
        not isinstance(requirement_spec, dict)
        or requirement_spec.get("confirmation_status") != "confirmed"
    ):
        return "await_user_input"
    clarification = state.get("clarification")
    return "await_user_input" if isinstance(clarification, dict) and clarification.get("status") == "requires_user_input" else "product_planning"


def _route_product_planning(state: ProjectState) -> str:
    """ProductPlan 未确认时结束当前轮次，否则进入 UI 设计。"""

    clarification = state.get("clarification")
    return "await_user_input" if isinstance(clarification, dict) and clarification.get("status") == "requires_user_input" else "ui_confirmation"


def _route_ui_confirmation(state: ProjectState) -> str:
    """UI设计稿未全部确认时结束当前轮次，否则进入技术规划。"""

    clarification = state.get("clarification")
    return "await_user_input" if isinstance(clarification, dict) and clarification.get("status") == "requires_user_input" else "technical_planning"


def _requirements(state: ProjectState) -> dict:
    """在需求节点前后同步工作区权威生命周期并记录错误。"""

    node_state = design_artifact_node_state(state, "requirements")
    workspace = _workspace(node_state)
    lifecycle = _ensure_lifecycle(node_state)
    try:
        if lifecycle.initialization.stage in {
            ApplicationLifecycleStage.COLLECTING_REQUIREMENT,
            ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
        }:
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                status=ApplicationLifecycleStatus.RUNNING,
                active_run_id=state.get("active_run_id"),
            )
        elif (
            lifecycle.initialization.stage == ApplicationLifecycleStage.ANALYZING_REQUIREMENT
            and lifecycle.initialization.status in {
                ApplicationLifecycleStatus.FAILED,
                ApplicationLifecycleStatus.CANCELLED,
            }
        ):
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                status=ApplicationLifecycleStatus.RUNNING,
                active_run_id=state.get("active_run_id"),
            )
        elif (
            lifecycle.initialization.stage
            == ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION
        ):
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
                status=ApplicationLifecycleStatus.RUNNING,
                active_run_id=state.get("active_run_id"),
            )
        update = nodes.requirements(node_state)
        lifecycle = _persist_requirement_result(workspace, update, node_state)
        return design_node_update(
            state,
            "requirements",
            {**update, "lifecycle": application_lifecycle_payload(lifecycle)},
        )
    except asyncio.CancelledError:
        _persist_node_cancelled(workspace, state)
        raise
    except Exception as exc:
        _persist_node_error(workspace, state, exc)
        raise


async def _ui_confirmation(state: ProjectState) -> dict:
    """为每个页面生成设计稿或处理明确跳过，并在阶段完成后进入技术规划。"""

    node_state = design_artifact_node_state(state, "ui_confirmation")
    if is_design_change(state) and not design_node_was_applied(state, "ui_confirmation"):
        node_state = prepare_ui_revision_state(node_state)
    workspace = _workspace(node_state)
    try:
        lifecycle = load_application_lifecycle(workspace) or _ensure_lifecycle(state)
        # 需求确认完成后推进到 UI设计生成阶段（若尚未推进）。
        if lifecycle.initialization.stage == ApplicationLifecycleStage.AWAITING_PRODUCT_PLAN_CONFIRMATION:
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
                status=ApplicationLifecycleStatus.RUNNING,
                active_run_id=state.get("active_run_id"),
            )
        update = await nodes.ui_confirmation(node_state)
        if update.get("status") != "completed":
            # 仅在当前阶段允许推进到 UI设计确认时才推进，避免恢复场景下的自转冲突。
            if (
                lifecycle.initialization.stage
                != ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION
            ):
                lifecycle = persist_application_lifecycle_transition(
                    workspace,
                    stage=ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
                    status=ApplicationLifecycleStatus.AWAITING_USER,
                    active_run_id=state.get("active_run_id"),
                )
            return design_node_update(
                state,
                "ui_confirmation",
                {
                    **update,
                    "workflow_scope": "application_planning",
                    "lifecycle": application_lifecycle_payload(lifecycle),
                },
            )
        # UI 已全部确认或明确跳过，推进到开发技术规划阶段。
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
            status=ApplicationLifecycleStatus.RUNNING,
            active_run_id=state.get("active_run_id"),
        )
        return design_node_update(
            state,
            "ui_confirmation",
            {
                **update,
                "workflow_scope": "application_planning",
                "lifecycle": application_lifecycle_payload(lifecycle),
            },
        )
    except asyncio.CancelledError:
        _persist_node_cancelled(workspace, state)
        raise
    except Exception as exc:
        _persist_node_error(workspace, state, exc)
        raise


def _product_planning(state: ProjectState) -> dict:
    """生成 ProductPlan，并把产品确认状态写入权威生命周期。"""

    node_state = design_artifact_node_state(state, "product_planning")
    workspace = _workspace(node_state)
    try:
        lifecycle = load_application_lifecycle(workspace) or _ensure_lifecycle(state)
        if (
            lifecycle.initialization.stage == ApplicationLifecycleStage.GENERATING_PRODUCT_PLAN
            and lifecycle.initialization.status
            in {ApplicationLifecycleStatus.FAILED, ApplicationLifecycleStatus.CANCELLED}
        ):
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.GENERATING_PRODUCT_PLAN,
                status=ApplicationLifecycleStatus.RUNNING,
                active_run_id=state.get("active_run_id"),
            )
        update = nodes.product_planning(node_state)
        if update.get("status") != "completed":
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.AWAITING_PRODUCT_PLAN_CONFIRMATION,
                status=ApplicationLifecycleStatus.AWAITING_USER,
                active_run_id=state.get("active_run_id"),
            )
            return design_node_update(
                state,
                "product_planning",
                {
                    **update,
                    "workflow_scope": "application_planning",
                    "lifecycle": application_lifecycle_payload(lifecycle),
                },
            )
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
            status=ApplicationLifecycleStatus.RUNNING,
            active_run_id=state.get("active_run_id"),
        )
        return design_node_update(
            state,
            "product_planning",
            {
                **update,
                "workflow_scope": "application_planning",
                "lifecycle": application_lifecycle_payload(lifecycle),
            },
        )
    except asyncio.CancelledError:
        _persist_node_cancelled(workspace, state)
        raise
    except Exception as exc:
        _persist_node_error(workspace, state, exc)
        raise


def _technical_planning(state: ProjectState) -> dict:
    """生成 TechnicalPlan，并在开发确认后校验全部正式产物。"""

    node_state = design_artifact_node_state(state, "technical_planning")
    workspace = _workspace(node_state)
    try:
        lifecycle = load_application_lifecycle(workspace) or _ensure_lifecycle(state)
        lifecycle = _prepare_technical_planning_lifecycle(workspace, lifecycle, state)
        if (
            lifecycle.initialization.stage
            == ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN
            and lifecycle.initialization.status in {
                ApplicationLifecycleStatus.FAILED,
                ApplicationLifecycleStatus.CANCELLED,
            }
        ):
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
                status=ApplicationLifecycleStatus.RUNNING,
                active_run_id=state.get("active_run_id"),
            )
        update = nodes.project_planning(node_state)
        if update.get("status") != "completed":
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
                status=ApplicationLifecycleStatus.AWAITING_USER,
                active_run_id=state.get("active_run_id"),
            )
            return design_node_update(
                state,
                "technical_planning",
                {
                    **update,
                    "workflow_scope": "application_planning",
                    "lifecycle": application_lifecycle_payload(lifecycle),
                },
            )
        merged_state = {**node_state, **update}
        confirmation = confirm_application_planning_artifacts(merged_state)
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
            status=ApplicationLifecycleStatus.RUNNING,
            active_run_id=state.get("active_run_id"),
        )
        # 技术规划完成即整条创建/变更链路终结，重置设计变更上下文，
        # 避免旧变更指令残留在 checkpoint 中影响后续轮次。
        return {
            **design_node_update(
                state,
                "technical_planning",
                {
                    **update,
                    "workflow_scope": "application_planning",
                    "application_planning_confirmation": confirmation,
                    "lifecycle": application_lifecycle_payload(lifecycle),
                },
            ),
            **cleared_design_change_context(),
        }
    except asyncio.CancelledError:
        _persist_node_cancelled(workspace, state)
        raise
    except Exception as exc:
        _persist_node_error(workspace, state, exc)
        raise


def _ensure_lifecycle(state: ProjectState):
    """从 Graph State 元数据创建或读取工作区生命周期。"""

    requirement_spec = state.get("requirement_spec")
    app_info = requirement_spec.get("app_info") if isinstance(requirement_spec, dict) else {}
    fallback_name = app_info.get("name") if isinstance(app_info, dict) else None
    application_id = str(state.get("project_id") or _workspace(state).split("/")[-1]).strip()
    application_name = str(state.get("application_name") or fallback_name or application_id).strip()
    return ensure_application_lifecycle(
        _workspace(state),
        application_id=application_id,
        application_name=application_name,
        initialization_thread_id=state.get("active_thread_id"),
        active_run_id=state.get("active_run_id"),
    )


def _prepare_technical_planning_lifecycle(workspace: str, lifecycle, state: ProjectState):
    """把当前生命周期推进到开发技术规划的合法状态路径。"""

    common = {
        "active_run_id": state.get("active_run_id"),
    }
    if lifecycle.initialization.stage == ApplicationLifecycleStage.COLLECTING_REQUIREMENT:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    if lifecycle.initialization.stage == ApplicationLifecycleStage.ANALYZING_REQUIREMENT:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    if (
        lifecycle.initialization.stage
        == ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC
    ):
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            **common,
        )
    if (
        lifecycle.initialization.stage
        == ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION
    ):
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_PRODUCT_PLAN,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    if lifecycle.initialization.stage == ApplicationLifecycleStage.GENERATING_PRODUCT_PLAN:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.AWAITING_PRODUCT_PLAN_CONFIRMATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            **common,
        )
    if lifecycle.initialization.stage == ApplicationLifecycleStage.AWAITING_PRODUCT_PLAN_CONFIRMATION:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    if lifecycle.initialization.stage == ApplicationLifecycleStage.GENERATING_UI_DESIGNS:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            **common,
        )
    if lifecycle.initialization.stage == ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    technical_plan = state.get("technical_plan")
    if (
        lifecycle.initialization.stage == ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN
        and isinstance(technical_plan, dict)
        and technical_plan.get("confirmation_status") in {
            "pending_user_confirmation",
            "confirmed",
        }
    ):
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            **common,
        )
    return lifecycle


def _persist_requirement_result(workspace: str, update: dict, state: ProjectState):
    """把需求节点结果映射为确定性生命周期阶段。"""

    mode = _clarification_mode(update)
    requirement_spec = update.get("requirement_spec")
    confirmation_status = (
        str(requirement_spec.get("confirmation_status") or "")
        if isinstance(requirement_spec, dict)
        else ""
    )
    common = {
        "active_run_id": state.get("active_run_id"),
    }
    # 澄清工具的 mode 由协议适配器生成且可能是 ask_user_question，不能用它判断业务阶段。
    if confirmation_status == "pending_user_input":
        return persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            **common,
        )
    current = load_application_lifecycle(workspace)
    if current is None:
        raise ValueError("需求节点完成后生命周期状态丢失。")
    if current.initialization.stage == ApplicationLifecycleStage.ANALYZING_REQUIREMENT:
        persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    if update.get("status") == "completed":
        # 需求确认完成后先进入 ProductPlan 产品确认。
        return persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_PRODUCT_PLAN,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    if confirmation_status != "pending_user_confirmation":
        raise ValueError(
            "需求节点返回了无法映射的 confirmation_status："
            f"{confirmation_status or '<missing>'}。"
        )
    return persist_application_lifecycle_transition(
        workspace,
        stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION,
        status=ApplicationLifecycleStatus.AWAITING_USER,
        **common,
    )


def _persist_node_error(workspace: str, state: ProjectState, exc: Exception) -> None:
    """把节点失败记录在当前阶段，避免错误时丢失恢复位置。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        return
    persist_application_lifecycle_transition(
        workspace,
        stage=current.initialization.stage,
        status=ApplicationLifecycleStatus.FAILED,
        active_run_id=state.get("active_run_id"),
        error=ApplicationLifecycleError(
            code="application_planning_failed",
            message=str(exc)[:2048] or type(exc).__name__,
            recoverable=True,
            occurredAt=utc_now(),
        ),
    )


def _persist_node_cancelled(workspace: str, state: ProjectState) -> None:
    """记录用户取消但保留同一阶段，供下次显式重试。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        return
    persist_application_lifecycle_transition(
        workspace,
        stage=current.initialization.stage,
        status=ApplicationLifecycleStatus.CANCELLED,
        active_run_id=state.get("active_run_id"),
    )


def _clarification_mode(update: dict) -> str:
    """安全提取节点的待交互模式。"""

    clarification = update.get("clarification")
    return str(clarification.get("mode") or "") if isinstance(clarification, dict) else ""


def _workspace(state: ProjectState) -> str:
    """校验并返回创建规划工作区。"""

    workspace = str(state.get("workspace") or "").strip()
    if not workspace:
        raise ValueError("创建应用规划必须提供 workspaceRoot。")
    return workspace


def build_application_planning_graph(*, checkpointer):
    """构建需求、产品、UI、技术四阶段创建规划 Graph。"""

    builder = StateGraph(ProjectState)
    builder.add_node("design_intent_analysis", analyze_design_intent)
    builder.add_node("design_chat_response", design_chat_response)
    builder.add_node("requirements", _requirements)
    builder.add_node("product_planning", _product_planning)
    builder.add_node("ui_confirmation", _ui_confirmation)
    builder.add_node("technical_planning", _technical_planning)
    builder.add_conditional_edges(START, _route_start, {
        "design_intent_analysis": "design_intent_analysis",
        "requirements": "requirements",
        "product_planning": "product_planning",
        "ui_confirmation": "ui_confirmation",
        "technical_planning": "technical_planning",
    })
    builder.add_conditional_edges("design_intent_analysis", route_design_intent, {
        "requirements": "requirements",
        "product_planning": "product_planning",
        "ui_confirmation": "ui_confirmation",
        "design_chat_response": "design_chat_response",
    })
    builder.add_conditional_edges("requirements", _route_requirements, {
        "product_planning": "product_planning",
        "await_user_input": END,
    })
    builder.add_conditional_edges("product_planning", _route_product_planning, {
        "ui_confirmation": "ui_confirmation",
        "await_user_input": END,
    })
    builder.add_conditional_edges("ui_confirmation", _route_ui_confirmation, {
        "technical_planning": "technical_planning",
        "await_user_input": END,
    })
    builder.add_edge("technical_planning", END)
    builder.add_edge("design_chat_response", END)
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
