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
    application_lifecycle_payload,
    ensure_application_lifecycle,
    load_application_lifecycle,
    persist_application_lifecycle_transition,
)
from app.services.application_revision_lifecycle import issue_revision_continuation
from app.services.authorization_frontend_projection import (
    apply_authorization_frontend_projection,
    compile_frontend_authorization_projection,
)
from app.services.frontend_scaffold import (
    collect_template_pages,
    ensure_frontend_page_placeholders,
)
from app.services.template_scaffold_injection import (
    inject_deterministic_backend_skeleton,
)
from app.workspace.plan_documents import technical_plan_json_path
from app.workspace.product_plan_documents import confirmed_product_plan_json_path
from app.workspace.spec_documents import ui_designs_json_path, load_ui_designs_json


def _route_start(state: ProjectState) -> str:
    """根据原创建规划 thread 的恢复点选择正常阶段或设计意图入口。"""

    resume_from = state.get("resume_from")
    if resume_from == "design_intent_analysis":
        return "design_intent_analysis"
    if resume_from in {
        "product_planning",
        "ui_confirmation",
        "planning_stage_entry",
        "technical_planning",
    }:
        return resume_from
    return "requirements"


def _route_requirements(state: ProjectState) -> str:
    """仅需求澄清问答挂起；需求确认与产品规划确认合并为产品规划门一次确认。"""

    clarification = state.get("clarification")
    clarification = clarification if isinstance(clarification, dict) else {}
    if (
        clarification.get("status") == "requires_user_input"
        and str(clarification.get("mode") or "") == "ask_user_question"
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
    """TechnicalPlan 未确认时进入原生审阅中断，确认后结束创建规划。"""

    clarification = state.get("clarification")
    return "technical_planning_review" if isinstance(clarification, dict) and clarification.get("status") == "requires_user_input" else "completed"


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
        revision_continuation: dict[str, Any] = {}
        active_revision = lifecycle.active_formal_revision
        if (
            active_revision is not None
            and active_revision.formal_branch.value
            in {"design_stage_revision", "workbench_plan_revision"}
        ):
            # 应用模板只在首次创建时生成一次。正式二次修改确认 TechnicalPlan
            # 后直接签发主 Workflow continuation，由 application_revision 收口并
            # 进入 inspect_workspace/prepare_build_tasks，不得再次进入模板阶段。
            # 在签发 continuation 前，把可确定性推导的后端骨架代码注入模板工程，
            # 让开发阶段 Agent 只需补充业务逻辑，不必从零生成 Entity/PO/Mapper 等
            # 确定性文件。模板工程已在首次创建时拉取到工作区，此处只写不删。
            _inject_revision_scaffold(workspace, node_state)
            token, issued = issue_revision_continuation(
                workspace,
                change_id=active_revision.change_id,
                technical_plan_path=(
                    Path(workspace) / ".xcodeagent" / "plans" / "technical-plan.json"
                ),
            )
            lifecycle = load_application_lifecycle(workspace) or lifecycle
            revision_continuation = {
                "changeId": issued.change_id,
                "formalBranch": issued.formal_branch.value,
                "action": "continue_revision_build",
                "token": token,
                "technicalPlanSha256": issued.technical_plan_sha256,
            }
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
    """校验规划阶段入口动作，并把生命周期推进到 TechnicalPlan 生成。"""

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
            stage=ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    if (
        lifecycle.initialization.stage
        == ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT
    ):
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            **common,
        )
    if lifecycle.initialization.stage == ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION:
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
            stage=ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            **common,
        )
    if lifecycle.initialization.stage == ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY:
        interaction = state.get("application_planning_interaction")
        action = str(interaction.get("action") or "") if isinstance(interaction, dict) else ""
        if action != "enter_planning":
            raise ValueError("TechnicalPlan 必须由用户明确进入规划阶段后才能生成。")
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


def _inject_revision_scaffold(workspace: str, state: dict[str, Any]) -> None:
    """二次修改确认 TechnicalPlan 后，把确定性代码增量注入模板工程。

    只在模板工程已存在时注入（首次创建走 prepare_template_generation，不在此注入）。
    注入失败不阻断主流程——确定性代码缺失时 Agent 仍可在 build 阶段补生成。

    注入内容：
    - 前端路由/权限资源（auth 分支）：从 TechnicalPlan 的 authorization_manifest 编译并写入 routes.tsx/resources.ts
    - 前端页面占位：从 ProductPlan + UiDesign 收集页面并创建占位文件
    - 后端骨架：从 TechnicalPlan 的 entities 推导 Entity/PO/Mapper/Repository/DTO/Controller
    全部幂等——已存在且内容一致的文件跳过，不覆盖用户手改。
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
        # 前端确定性注入：路由/权限资源（auth 分支）
        _inject_frontend_authorization(workspace, technical_plan)
        # 前端确定性注入：页面占位文件
        _inject_frontend_page_placeholders(workspace, state)
        # 后端确定性注入：Entity/PO/Mapper/Repository/DTO/Controller 骨架
        inject_deterministic_backend_skeleton(workspace, technical_plan)
    except Exception:
        # 确定性注入是优化项，失败不阻断二次修改主流程；Agent 仍可补生成。
        pass


def _inject_frontend_authorization(workspace: str, technical_plan: dict[str, Any]) -> None:
    """auth 分支模板：从 TechnicalPlan 编译并写入前端路由/权限资源。"""

    frontend_dir = Path(workspace) / "frontend"
    routes_path = frontend_dir / "src" / "constants" / "routes.tsx"
    if not routes_path.is_file():
        return  # 非 auth 分支或模板未拉取，跳过
    projection = compile_frontend_authorization_projection(technical_plan)
    if projection is not None:
        apply_authorization_frontend_projection(workspace, projection)


def _inject_frontend_page_placeholders(workspace: str, state: dict[str, Any]) -> None:
    """从 ProductPlan + UiDesign 收集页面并创建占位文件（main/auth 通用）。"""

    frontend_dir = Path(workspace) / "frontend"
    if not (frontend_dir / "src").is_dir():
        return  # 模板未拉取，跳过
    product_plan_path = confirmed_product_plan_json_path(state)
    if not product_plan_path.is_file():
        return
    import json
    with product_plan_path.open(encoding="utf-8") as handle:
        product_plan = json.load(handle)
    if not isinstance(product_plan, dict):
        return
    ui_designs_path = ui_designs_json_path(state)
    ui_designs = load_ui_designs_json(ui_designs_path) if ui_designs_path.is_file() else {}
    pages = collect_template_pages(product_plan, ui_designs)
    if pages:
        ensure_frontend_page_placeholders(frontend_dir, pages)


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
    builder.add_conditional_edges(START, _route_start, {
        "design_intent_analysis": "design_intent_analysis",
        "requirements": "requirements",
        "product_planning": "product_planning",
        "ui_confirmation": "ui_confirmation",
        "planning_stage_entry": "planning_stage_entry",
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
        "completed": END,
    })
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
