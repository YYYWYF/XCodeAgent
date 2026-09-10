from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.application_planning_revision import (
    DESIGN_CHANGE_TARGET_NODES,
    analyze_design_intent,
    cleared_design_change_context,
    design_artifact_node_state,
    design_chat_response,
    design_node_update,
    is_design_change,
    prepare_ui_revision_state,
    route_design_intent,
)
from app.graph.application_planning_interrupts import (
    pending_review_node,
    planning_stage_entry,
    requirement_document_review,
    requirements_review,
    technical_planning_review,
    ui_confirmation_review,
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
    ApplicationLifecycleConflictError,
    application_lifecycle_payload,
    ensure_application_lifecycle,
    load_application_lifecycle,
    persist_application_lifecycle_transition,
)
from app.services.application_revision_lifecycle import (
    issue_revision_continuation,
    update_active_revision_progress,
)
from app.config import Settings
from app.services.artifact_invalidation import canonical_sha256
from app.services.template_reconcile.finalization import (
    claim_template_reconcile_finalization,
    mark_template_reconcile_failed,
)
from app.services.template_reconcile.service import (
    TemplateReconcileService,
    reconcile_mode_for_requested_config,
)
from app.services.template_reconcile.runtime_v2 import load_current_attempt
from app.services.template_reconcile.state_v2 import load_template_state_v2
from app.services.template_reconcile.template_preparation import (
    template_preparation_projection_v2,
)
from app.services.template_scaffold_injection import (
    inject_deterministic_backend_skeleton,
)
from app.services.workspace_bootstrap.requested_config import compile_template_requested_config
from app.workspace.plan_documents import technical_plan_json_path


def _route_start(state: ProjectState) -> str:
    """根据原创建规划 thread 的恢复点选择正常阶段或设计意图入口。"""

    resume_from = str(state.get("resume_from") or "").strip()
    workspace = str(state.get("workspace") or state.get("workspace_path") or "").strip()
    lifecycle = load_application_lifecycle(workspace) if workspace else None
    if resume_from == "design_intent_analysis":
        return "design_intent_analysis"
    if (
        resume_from == "technical_planning"
        and lifecycle is not None
        and lifecycle.active_formal_revision is not None
        and lifecycle.active_formal_revision.status == "template_reconcile_failed"
    ):
        # Reconcile 失败后的恢复必须复用已确认的 TechnicalPlan，不能重放旧确认动作。
        return "template_reconcile"
    allowed_resume_stages = {
        "requirements": {
            ApplicationLifecycleStage.COLLECTING_REQUIREMENT,
            ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
            ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
        },
        "product_planning": {
            ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
            ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
        },
        "ui_confirmation": {
            ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
            ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
        },
        "planning_stage_entry": {
            ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
        },
        "technical_planning": {
            ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
            ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
        },
    }
    if resume_from in allowed_resume_stages:
        if (
            lifecycle is not None
            and lifecycle.initialization.stage
            not in allowed_resume_stages[resume_from]
        ):
            raise ApplicationLifecycleConflictError(
                "application_planning 恢复入口与 lifecycle 不匹配："
                f"resume_from={resume_from}，"
                f"lifecycle={lifecycle.initialization.stage.value}。"
            )
        return resume_from
    if resume_from:
        raise ApplicationLifecycleConflictError(
            f"application_planning 不支持恢复入口：{resume_from}"
        )
    if (
        lifecycle is None
        or lifecycle.initialization.stage
        == ApplicationLifecycleStage.COLLECTING_REQUIREMENT
    ):
        return "requirements"
    raise ApplicationLifecycleConflictError(
        "application_planning 已存在生命周期，但本次执行缺少明确的 "
        "interaction 或 resume_from，当前 lifecycle="
        f"{lifecycle.initialization.stage.value}。"
    )


def _route_requirements(state: ProjectState) -> str:
    """把所有未完成的需求输入挂起，需求草稿完成后才进入联合产品规划门。"""

    clarification = state.get("clarification")
    clarification = clarification if isinstance(clarification, dict) else {}
    requirement_spec = state.get("requirement_spec")
    confirmation_status = (
        str(requirement_spec.get("confirmation_status") or "")
        if isinstance(requirement_spec, dict)
        else ""
    )
    # 权限初始化等确定性问题也属于 RequirementSpec 的待回答状态。只按
    # ask_user_question 判断会让 Graph 越过 interrupt 直接运行 ProductPlan，
    # 从而同时破坏生命周期转换和前端可恢复确认卡。
    if (
        clarification.get("status") == "requires_user_input"
        and confirmation_status == "pending_user_input"
    ):
        return "requirements_review"
    return "product_planning"


def _route_product_planning(state: ProjectState) -> str:
    """联合需求文档未确认时进入原生审阅中断，否则进入 UI 设计。"""

    clarification = state.get("clarification")
    return "requirement_document_review" if isinstance(clarification, dict) and clarification.get("status") == "requires_user_input" else "ui_confirmation"


def _route_ui_confirmation(state: ProjectState) -> str:
    """UI设计稿未全部确认时继续审阅，否则停在独立规划阶段入口。"""

    clarification = state.get("clarification")
    return "ui_confirmation_review" if isinstance(clarification, dict) and clarification.get("status") == "requires_user_input" else "planning_stage_entry"


def _route_technical_planning(state: ProjectState) -> str:
    """TechnicalPlan 未确认时进入原生审阅中断，确认后转入独立收口阶段。"""

    clarification = state.get("clarification")
    if isinstance(clarification, dict) and clarification.get("status") == "requires_user_input":
        return "technical_planning_review"
    return "template_reconcile" if state.get("template_reconcile_pending") else "completed"


def _requirements(state: ProjectState) -> dict:
    """在需求节点前后同步工作区权威生命周期并记录错误。"""

    node_state = design_artifact_node_state(state, "requirements")
    workspace = _workspace(node_state)
    lifecycle = _ensure_lifecycle(node_state)
    interaction = state.get("application_planning_interaction")
    interaction_action = (
        str(interaction.get("action") or "")
        if isinstance(interaction, dict)
        else ""
    )
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
            == ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION
        ):
            if interaction_action == "revise":
                # 用户补充/修改会使上一版草稿失效，必须回到分析阶段，不能直接进入文档生成。
                lifecycle = persist_application_lifecycle_transition(
                    workspace,
                    stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                    status=ApplicationLifecycleStatus.RUNNING,
                    active_run_id=state.get("active_run_id"),
                )
            else:
                lifecycle = persist_application_lifecycle_transition(
                    workspace,
                    stage=ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
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
    """为每个页面生成设计稿或处理明确跳过，完成后等待用户进入规划阶段。"""

    node_state = design_artifact_node_state(state, "ui_confirmation")
    if (
        is_design_change(state)
        and state.get("design_change_generation_target") == "ui_confirmation"
        and not node_state.get("application_planning_interaction")
    ):
        node_state = prepare_ui_revision_state(node_state)
    workspace = _workspace(node_state)
    try:
        lifecycle = load_application_lifecycle(workspace) or _ensure_lifecycle(state)
        # 需求确认完成后推进到 UI设计生成阶段（若尚未推进）。
        if lifecycle.initialization.stage == ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION:
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
        # UI 已全部确认或明确跳过，只推进到规划阶段入口，不得自动生成 TechnicalPlan。
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            active_run_id=state.get("active_run_id"),
        )
        lifecycle = _sync_design_revision_artifact(
            workspace,
            lifecycle,
            current_artifact="ui-design",
            remaining_artifacts=["technical-plan"],
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
    """生成并联合确认需求文档，并把结果写入权威生命周期。"""

    node_state = design_artifact_node_state(state, "product_planning")
    workspace = _workspace(node_state)
    try:
        lifecycle = load_application_lifecycle(workspace) or _ensure_lifecycle(state)
        if (
            lifecycle.initialization.stage
            == ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT
        ):
            # RequirementSpec 草稿已通过校验后，继续生成同一联合阶段的 ProductPlan 草稿。
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
                status=ApplicationLifecycleStatus.RUNNING,
                active_run_id=state.get("active_run_id"),
            )
        update = nodes.product_planning(node_state)
        if update.get("status") != "completed":
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
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
        lifecycle = _sync_design_revision_artifact(
            workspace,
            lifecycle,
            current_artifact="product-plan",
            remaining_artifacts=["ui-design", "technical-plan"],
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
        if (
            lifecycle.initialization.stage
            == ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN
        ):
            # 只有本轮节点已经返回完成结果后才补齐确认边；这既允许正常确认
            # 进入模板/continuation，也不会再被 checkpoint 中的旧计划提前触发。
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
                status=ApplicationLifecycleStatus.AWAITING_USER,
                active_run_id=state.get("active_run_id"),
            )
        merged_state = {**node_state, **update}
        confirmation = confirm_application_planning_artifacts(merged_state)
        lifecycle = _sync_design_revision_artifact(
            workspace,
            lifecycle,
            current_artifact="technical-plan",
            remaining_artifacts=[],
        )
        revision_continuation: dict[str, Any] = {}
        template_reconcile_pending = False
        active_revision = lifecycle.active_formal_revision
        if (
            active_revision is not None
            and active_revision.formal_branch.value
            in {"design_stage_revision", "workbench_plan_revision"}
        ):
            # 确认产物必须先独立提交 checkpoint；后续模板更新失败不得重放 confirm。
            template_reconcile_pending = True
        else:
            # 只有首次创建流程会在 TechnicalPlan 确认后准备应用模板。
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
                    "revision_continuation": revision_continuation,
                    "template_reconcile_pending": template_reconcile_pending,
                    # confirm 已由本节点写入 canonical TechnicalPlan；禁止后续节点重放它。
                    "application_planning_interaction": {},
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


def _reconcile_confirmed_revision(state: ProjectState) -> dict:
    """对已确认 TechnicalPlan 执行可重试的模板收口，并在成功后签发 continuation。"""

    workspace = _workspace(state)
    lifecycle = load_application_lifecycle(workspace)
    active_revision = lifecycle.active_formal_revision if lifecycle is not None else None
    if active_revision is None:
        raise ApplicationLifecycleConflictError("Template Reconcile 缺少 active formal revision。")
    try:
        # 该节点只读取 canonical TechnicalPlan；确认节点已在前一 checkpoint 提交完成。
        _reconcile_revision_template_capabilities(workspace, active_revision.change_id)
        _inject_revision_backend_skeleton(workspace, state, strict=True)
        token, issued = issue_revision_continuation(
            workspace,
            change_id=active_revision.change_id,
            technical_plan_path=(
                Path(workspace) / ".xcodeagent" / "plans" / "technical-plan.json"
            ),
        )
        lifecycle = load_application_lifecycle(workspace) or lifecycle
        return {
            **design_node_update(
                state,
                "template_reconcile",
                {
                    "phase": "template_reconcile",
                    "status": "completed",
                    "template_reconcile_pending": False,
                    "template_preparation": template_preparation_projection_v2(workspace),
                    "application_planning_interaction": {},
                    "revision_continuation": {
                        "changeId": issued.change_id,
                        "formalBranch": issued.formal_branch.value,
                        "action": "continue_revision_build",
                        "token": token,
                        "technicalPlanSha256": issued.technical_plan_sha256,
                    },
                    "lifecycle": application_lifecycle_payload(lifecycle),
                },
            ),
            **cleared_design_change_context(),
        }
    except asyncio.CancelledError:
        _persist_node_cancelled(workspace, state)
        raise
    except Exception as exc:
        latest = load_application_lifecycle(workspace)
        if (
            latest is not None
            and latest.active_formal_revision is not None
            and latest.active_formal_revision.change_id == active_revision.change_id
            and latest.active_formal_revision.status == "template_reconciling"
        ):
            mark_template_reconcile_failed(workspace, change_id=active_revision.change_id)
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
    """校验规划阶段入口动作，并把生命周期推进到 TechnicalPlan 生成。"""

    common = {
        "active_run_id": state.get("active_run_id"),
    }
    interaction = state.get("application_planning_interaction")
    action = str(interaction.get("action") or "") if isinstance(interaction, dict) else ""
    if lifecycle.initialization.stage == ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY:
        if action != "enter_planning":
            raise ValueError("TechnicalPlan 必须由用户明确进入规划阶段后才能生成。")
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    elif (
        lifecycle.initialization.stage
        == ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION
        and action == "revise"
    ):
        # 用户要求重做当前 TechnicalPlan 时先回到生成态；确认动作则继续留在
        # awaiting 状态，由 project_planning 同步 Markdown 并完成确认。
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    if lifecycle.initialization.stage not in {
        ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
        ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
    }:
        raise ValueError(
            "TechnicalPlan 只能在用户明确进入规划阶段后生成，当前生命周期为 "
            f"{lifecycle.initialization.stage.value}。"
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
    if update.get("status") == "completed":
        # 需求确认完成后先进入 ProductPlan 产品确认。
        return persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
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
        stage=ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
        status=ApplicationLifecycleStatus.RUNNING,
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


def _reconcile_revision_template_capabilities(workspace: str, change_id: str) -> None:
    """在二次 TechnicalPlan 确认后、Skeleton 前执行一次受开关保护的模板能力补充。"""

    settings = Settings.from_env()
    if not settings.template_reconcile_enabled:
        return
    plan_path = Path(workspace) / ".xcodeagent" / "plans" / "technical-plan.json"
    claim = claim_template_reconcile_finalization(
        workspace,
        change_id=change_id,
        technical_plan_path=plan_path,
    )
    if not claim.acquired:
        raise ApplicationLifecycleConflictError("Template Reconcile 已在执行或等待后续收口，不能重复触发。")
    try:
        requested_config = compile_template_requested_config(workspace)
        mode = reconcile_mode_for_requested_config(
            load_template_state_v2(workspace), requested_config
        )
        service = TemplateReconcileService(settings)
        arguments = {
            "workspace": workspace,
            "change_id": change_id,
            "requested_config": requested_config,
            "technical_plan_sha256": canonical_sha256(plan_path),
            "mode": mode,
        }
        # 用户从 template_reconcile_failed 恢复时必须产生新 Attempt；运行中 Attempt 才走 crash recovery。
        if (attempt := load_current_attempt(workspace)) is not None and attempt.status == "FAILED" and attempt.phase == "FAILED":
            asyncio.run(service.retry_template_preparation(**arguments))
        else:
            asyncio.run(service.reconcile(**arguments))
    except Exception:
        # 外层同时覆盖 Skeleton 失败，并统一把 active revision 标记为可重试失败。
        raise


def _sync_design_revision_artifact(
    workspace: str,
    lifecycle: Any,
    *,
    current_artifact: str,
    remaining_artifacts: list[str],
) -> Any:
    """在设计阶段正式产物确认后同步 active revision 的精确进度。"""

    active = lifecycle.active_formal_revision
    if active is None or active.formal_branch.value != "design_stage_revision":
        return lifecycle
    update_active_revision_progress(
        workspace,
        change_id=active.change_id,
        status="design_planning",
        current_artifact=current_artifact,
        remaining_artifacts=remaining_artifacts,
    )
    return load_application_lifecycle(workspace) or lifecycle


def _inject_revision_backend_skeleton(
    workspace: str,
    state: dict[str, Any],
    *,
    strict: bool = False,
) -> None:
    """二次修改确认 TechnicalPlan 后，仅注入确定性的后端骨架。

    只在模板工程已存在时注入（首次创建由 Workspace Bootstrap 完成，不在此注入）。
    旧流程注入失败不阻断主流程；当 `strict=True`（模板能力 Reconcile 已成功）时，
    必须把失败上抛，使同一 changeId 可从已完成 Reconcile 结果重试。

    前端页面、菜单、路由及权限资源均由确认 Build DAG 在页面任务完成后投影；
    这里不得预创建或重写它们。后端骨架从 TechnicalPlan 的 entities 推导
    Entity/PO/Mapper/Repository/DTO/Controller，并保持幂等。
    """

    try:
        plan_path = technical_plan_json_path(state)
        if not plan_path.is_file():
            return
        import json
        with plan_path.open(encoding="utf-8") as handle:
            technical_plan = json.load(handle)
        if not isinstance(technical_plan, dict):
            return
        # 后端确定性注入：Entity/PO/Mapper/Repository/DTO/Controller 骨架
        inject_deterministic_backend_skeleton(workspace, technical_plan)
    except Exception:
        # 后端骨架是优化项，失败不阻断二次修改主流程；Agent 仍可补生成。
        if strict:
            raise


def build_application_planning_graph(*, checkpointer):
    """构建设计、规划分段且含显式入口门禁的创建规划 Graph。"""

    builder = StateGraph(ProjectState)
    builder.add_node("design_intent_analysis", analyze_design_intent)
    builder.add_node("design_chat_response", design_chat_response)
    builder.add_node("requirements", _requirements)
    builder.add_node("requirements_review", requirements_review)
    builder.add_node("product_planning", _product_planning)
    builder.add_node("requirement_document_review", requirement_document_review)
    builder.add_node("ui_confirmation", _ui_confirmation)
    builder.add_node("ui_confirmation_review", ui_confirmation_review)
    builder.add_node("planning_stage_entry", planning_stage_entry)
    builder.add_node("technical_planning", _technical_planning)
    builder.add_node("technical_planning_review", technical_planning_review)
    builder.add_node("template_reconcile", _reconcile_confirmed_revision)
    builder.add_conditional_edges(START, _route_start, {
        "design_intent_analysis": "design_intent_analysis",
        "requirements": "requirements",
        "product_planning": "product_planning",
        "ui_confirmation": "ui_confirmation",
        "planning_stage_entry": "planning_stage_entry",
        "technical_planning": "technical_planning",
        "template_reconcile": "template_reconcile",
    })
    builder.add_conditional_edges("design_intent_analysis", route_design_intent, {
        "requirements": "requirements",
        "product_planning": "product_planning",
        "ui_confirmation": "ui_confirmation",
        "design_chat_response": "design_chat_response",
    })
    builder.add_conditional_edges("requirements", _route_requirements, {
        "product_planning": "product_planning",
        "requirements_review": "requirements_review",
    })
    builder.add_conditional_edges("product_planning", _route_product_planning, {
        "ui_confirmation": "ui_confirmation",
        "requirement_document_review": "requirement_document_review",
    })
    builder.add_conditional_edges("ui_confirmation", _route_ui_confirmation, {
        "planning_stage_entry": "planning_stage_entry",
        "ui_confirmation_review": "ui_confirmation_review",
    })
    builder.add_conditional_edges("technical_planning", _route_technical_planning, {
        "technical_planning_review": "technical_planning_review",
        "template_reconcile": "template_reconcile",
        "completed": END,
    })
    builder.add_edge("template_reconcile", END)
    builder.add_conditional_edges("design_chat_response", pending_review_node, {
        "requirements_review": "requirements_review",
        "requirement_document_review": "requirement_document_review",
        "ui_confirmation_review": "ui_confirmation_review",
        "planning_stage_entry": "planning_stage_entry",
        "technical_planning_review": "technical_planning_review",
    })
    return builder.compile(checkpointer=checkpointer)


_APPLICATION_PLANNING_GRAPHS: dict[str, tuple[object, object]] = {}


async def application_planning_graph_for_request(*, workspace: str | None = None, project_id: str | None = None):
    """按工作区复用独立创建规划 Graph 与 SQLite checkpointer。"""

    db_path = workflow_checkpoint_db_path(workspace=workspace, project_id=project_id)
    cache_key = str(db_path)
    checkpointer = await workflow_checkpointer(workspace=workspace, project_id=project_id)
    cached = _APPLICATION_PLANNING_GRAPHS.get(cache_key)
    if cached is None or cached[0] is not checkpointer:
        cached = (
            checkpointer,
            build_application_planning_graph(checkpointer=checkpointer),
        )
        _APPLICATION_PLANNING_GRAPHS[cache_key] = cached
    return cached[1]


def clear_application_planning_graph_cache(*, cache_key: str | None = None) -> None:
    """清理全部或单个 checkpoint 数据库对应的创建规划 Graph 缓存。"""

    if cache_key is None:
        _APPLICATION_PLANNING_GRAPHS.clear()
    else:
        _APPLICATION_PLANNING_GRAPHS.pop(cache_key, None)
