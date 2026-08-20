import logging

from copy import deepcopy
from typing import Any

from langgraph.config import get_stream_writer

from app.agents.main.document_sync import sync_project_plan_from_markdown
from app.agents.database.generator import (
    entity_design_bindings_with_agent,
    entity_design_table_selection_with_agent,
    generate_database_with_deep_agent,
)
from app.agents.main.planner import (
    plan_project_with_chat_model,
    revise_project_plan_with_chat_model,
)
from app.agents.main.page_designer import (
    PageDependencyGapError,
    design_endpoint_with_chat_model,
    design_page_with_chat_model,
)
from app.graph.nodes.confirmation import user_confirmed_text
from app.graph.nodes.common import workspace_from_state
from app.graph.state import ProjectState
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.detail_review import (
    apply_detail_review_submission,
    detail_review_payload,
)
from app.services.frontend_page_tree import (
    project_plan_page_records,
    update_frontend_page_leaves,
)
from app.services.entity_design import (
    ENTITY_DESIGN_STAGE_EXTERNAL_API_INPUT,
    ENTITY_DESIGN_STAGE_REVIEW_READY,
    apply_complete_entity_design,
    apply_entity_design_action,
    entity_bound_design_gate,
    entity_design_validation_errors,
    execute_entity_database_operations,
)
from app.services.entity_design_assist import entity_design_ai_suggestions
from app.services.entity_detail_plan import (
    attach_entity_detail_plan,
    create_entity_detail_plan,
)
from app.services.project_plan import (
    apply_project_plan_datasource_policy,
    apply_project_plan_feedback,
    TECHNICAL_PLAN_ARTIFACT_TYPE,
    validate_technical_plan_api_contracts,
    validate_project_plan_datasource_policy,
)
from app.services.product_plan import require_current_product_plan
from app.services.page_dependencies import validate_project_plan_dependencies
from app.services.page_implementation_contract import (
    attach_page_implementation_contracts,
    materialize_technical_plan_runtime,
    validate_page_implementation_contracts,
)
from app.services.page_detail_plan import (
    attach_endpoint_detail_plan,
    attach_page_detail_plan,
    detail_design_targets,
    extract_endpoint_detail_context,
    extract_page_detail_context,
)
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.plan_documents import (
    edited_technical_plan_markdown,
    edited_project_plan_markdown,
    project_plan_json_path,
    technical_plan_json_path,
    write_technical_plan_document,
    write_project_plan_document,
)


logger = logging.getLogger("uvicorn.error")
_TECHNICAL_PLAN_GENERATION_ATTEMPTS = 3


class EntityDesignRequiredError(ValueError):
    """接口/页面详细设计开始前，绑定的实体尚未完成实体设计并确认。"""

    def __init__(
        self,
        reason: str,
        missing_entities: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.missing_entities = missing_entities or []


def _detail_workspace_options(state: ProjectState) -> dict[str, str]:
    """仅在状态包含工作区时向详情生成链路传递绑定参数。"""

    workspace_root = workspace_from_state(state)
    return {"workspace_root": workspace_root} if workspace_root else {}


def _planning_token_callback(token: str) -> None:
    """将规划模型流式 token 转发到 LangGraph custom stream。"""

    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        return
    writer({"type": "llm.token", "token": token, "node": "project_planning"})


def _planning_phase(state: ProjectState) -> str:
    """区分创建流程的开发技术规划与主工作流项目规划阶段。"""

    return (
        "technical_planning"
        if state.get("workflow_scope") == "application_planning"
        else "project_planning"
    )


def _application_planning_interaction(state: ProjectState) -> dict[str, Any]:
    """读取当前创建规划的结构化 TechnicalPlan 动作。"""

    value = state.get("application_planning_interaction")
    return value if isinstance(value, dict) and value else {}


def _planning_request(state: ProjectState, interaction: dict[str, Any]) -> str:
    """选择技术规划请求，创建流程只使用结构化交互提供的文本。"""

    if state.get("workflow_scope") == "application_planning" and interaction:
        return str(interaction.get("request") or "").strip()
    return str(state.get("request") or "")


def _technical_ui_manifest(value: object) -> dict:
    """移除 UI TSX 正文，只向技术规划模型提供路径、哈希和控件索引。"""

    designs = value if isinstance(value, dict) else {}
    pages = designs.get("pages") if isinstance(designs.get("pages"), list) else []
    return {
        "schema_version": designs.get("schema_version"),
        "confirmation_status": designs.get("confirmation_status"),
        "product_plan_sha256": designs.get("product_plan_sha256"),
        "pages": [
            {
                key: page.get(key)
                for key in (
                    "pageId",
                    "page_key",
                    "preview_path",
                    "code_path",
                    "code_sha256",
                    "bindings",
                    "verification",
                    "status",
                )
                if page.get(key) is not None
            }
            for page in pages
            if isinstance(page, dict)
        ],
    }


def _technical_planning_requirement_spec(
    state: ProjectState,
    requirement_spec: dict,
) -> dict:
    """把已确认 ProductPlan 注入技术规划输入，并保留 UI 阶段门禁。"""

    if state.get("workflow_scope") != "application_planning":
        return requirement_spec
    product_plan = state.get("product_plan")
    ui_designs = state.get("ui_designs")
    if not isinstance(product_plan, dict) or product_plan.get("confirmation_status") != "confirmed":
        raise ValueError("TechnicalPlan 必须基于已确认 ProductPlan 生成。")
    if not isinstance(ui_designs, dict) or ui_designs.get("confirmation_status") not in {
        "confirmed",
        "skipped",
    }:
        raise ValueError("TechnicalPlan 必须基于已确认或已跳过的 UI 设计阶段生成。")
    return {
        **requirement_spec,
        "pages": product_plan.get("pages", requirement_spec.get("pages", [])),
        "confirmed_product_plan": product_plan,
    }


def _attach_technical_plan_contracts(
    state: ProjectState,
    plan: dict,
) -> dict:
    """为当前 TechnicalPlan 写入上游哈希；实现契约只在运行时编译。"""

    if state.get("workflow_scope") != "application_planning":
        return plan
    product_plan = state.get("product_plan")
    ui_designs = state.get("ui_designs")
    if not isinstance(product_plan, dict) or not isinstance(ui_designs, dict):
        raise ValueError("TechnicalPlan 缺少 ProductPlan 或 UiDesign 输入。")
    return attach_page_implementation_contracts(plan, product_plan, ui_designs)


def _technical_plan_contract_errors(
    state: ProjectState,
    plan: dict,
) -> list[str]:
    """校验 TechnicalPlan 页面实现契约，并保留错误供模型自动修复。"""

    if state.get("workflow_scope") != "application_planning":
        return []
    product_plan = state.get("product_plan")
    ui_designs = state.get("ui_designs")
    if not isinstance(product_plan, dict) or not isinstance(ui_designs, dict):
        return ["TechnicalPlan 缺少 ProductPlan 或 UiDesign 输入。"]
    return validate_page_implementation_contracts(plan, product_plan, ui_designs)


def _planning_artifact_fields(
    state: ProjectState,
    plan: dict,
    project_plan_path: str,
) -> dict:
    """写入 TechnicalPlan 正式副本并返回新旧状态字段。"""

    if state.get("workflow_scope") != "application_planning":
        return {}
    technical_path, technical_json_path = write_technical_plan_document(state, plan)
    return {
        "technical_plan": plan,
        "technical_plan_path": technical_path,
        "technical_plan_json_path": technical_json_path,
        "project_plan_path": project_plan_path,
    }


def _planning_confirmation_payload(state: ProjectState, plan: dict) -> dict:
    """按业务范围返回产品可见或开发可见的计划确认载荷。"""

    if state.get("workflow_scope") == "application_planning":
        return _technical_plan_confirmation_payload(plan)
    return _project_plan_confirmation_payload(plan)


def _technical_plan_confirmation_payload(technical_plan: dict) -> dict:
    """构造只面向开发审核的 TechnicalPlan 确认载荷。"""

    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="技术规划确认",
                question=(
                    "请由开发角色确认技术架构、业务实体、API、Schema、工程设计和页面 endpoint 引用。"
                    "正确时回复“确认技术规划，继续”；需要调整时直接写出技术修改意见。"
                ),
                type="text",
                placeholder="例如：确认技术规划，继续 / 为订单列表补充分页接口。",
            )
        ]
    )
    payload["mode"] = "technical_plan_confirmation"
    payload["message"] = "请由开发角色确认技术规划后再进入工作区。"
    payload["plan_summary"] = technical_plan.get("artifact_type", "technical-plan")
    return payload


def _planning_confirmed_payload(state: ProjectState, plan: dict) -> dict:
    """按业务范围返回计划已确认载荷。"""

    if state.get("workflow_scope") == "application_planning":
        return {
            "mode": "technical_plan_confirmation",
            "status": "clear",
            "question_schema": "gemini_cli.ask_user.v1",
            "questions": [],
            "assumptions": [],
            "message": "技术规划已由开发角色确认，可以进入工作区。",
            "plan_summary": plan.get("artifact_type", "technical-plan"),
        }
    return _project_plan_confirmed_payload(plan)


def _detail_progress(message: str, **detail: object) -> None:
    """向 LangGraph custom stream 和后端日志同步发送细节设计进度。"""

    logger.info("detail_confirmation progress: %s %s", message, detail)
    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        writer = None
    if writer:
        writer(
            {
                "type": "detail_confirmation.progress",
                "node_name": "detail_confirmation",
                "message": message,
                "detail": detail,
            }
        )


def project_planning(state: ProjectState) -> dict:
    """生成或确认 ProjectPlan/TechnicalPlan，并保留独立实体设计边界。"""

    phase = _planning_phase(state)
    interaction = _application_planning_interaction(state)
    application_planning_scope = state.get("workflow_scope") == "application_planning"
    request = _planning_request(state, interaction)
    action = str(interaction.get("action") or "")
    if state.get("workflow_scope") == "application_planning":
        requirement_spec = state.get("requirement_spec")
        if not isinstance(requirement_spec, dict):
            raise ValueError("TechnicalPlan 必须读取已确认的 RequirementSpec。")
        product_plan = require_current_product_plan(state.get("product_plan"), requirement_spec)
        if product_plan.get("confirmation_status") != "confirmed":
            raise ValueError("TechnicalPlan 必须基于已确认 ProductPlan 生成。")
    existing_plan = (
        state.get("technical_plan")
        if state.get("workflow_scope") == "application_planning"
        else state.get("project_plan")
    )
    if isinstance(existing_plan, dict):
        existing_plan = apply_project_plan_datasource_policy(existing_plan)
    if (
        isinstance(existing_plan, dict)
        and existing_plan.get("confirmation_status") == "pending_user_confirmation"
        and not _has_explicit_user_submission(state)
    ):
        pending_errors: list[str] = []
        if phase == "technical_planning":
            existing_plan = _attach_technical_plan_contracts(state, existing_plan)
            pending_errors = _project_plan_validation_errors(
                existing_plan,
                state,
            )
        return {
            "phase": phase,
            "status": "requires_user_input",
            "project_plan": existing_plan,
            **({"technical_plan": existing_plan} if phase == "technical_planning" else {}),
            "project_plan_path": state.get("project_plan_path", ""),
            "project_plan_json_path": state.get("project_plan_json_path", ""),
            "technical_plan_path": state.get("technical_plan_path", ""),
            "technical_plan_json_path": state.get("technical_plan_json_path", ""),
            "clarification": (
                _project_plan_dependency_error_payload(pending_errors)
                if pending_errors
                else _planning_confirmation_payload(state, existing_plan)
            ),
            "timeline": [phase],
        }
    if existing_plan and (
        action == "confirm"
        if application_planning_scope
        else _user_confirmed_project_plan(request)
    ):
        edited_markdown = (
            edited_technical_plan_markdown(state, existing_plan)
            if phase == "technical_planning"
            else edited_project_plan_markdown(state, existing_plan)
        )
        synchronized_plan = (
            sync_project_plan_from_markdown(
                existing_plan,
                state.get("requirement_spec", {}),
                edited_markdown,
            )
            if edited_markdown is not None
            else existing_plan
        )
        project_plan = {
            **apply_project_plan_feedback(
                synchronized_plan,
                request,
            ),
            "confirmation_status": "confirmed",
        }
        project_plan = _attach_technical_plan_contracts(state, project_plan)
        validation_errors = _project_plan_validation_errors(
            project_plan,
            state,
        )
        if validation_errors:
            repaired_plan, remaining_errors = _repair_project_plan_validation_errors(
                project_plan,
                validation_errors,
                state=state,
                repair_attempts=_TECHNICAL_PLAN_GENERATION_ATTEMPTS,
            )
            repaired_path = write_project_plan_document(state, repaired_plan)
            return {
                "phase": phase,
                "status": "requires_user_input",
                "project_plan": repaired_plan,
                "project_plan_path": repaired_path,
                "project_plan_json_path": _project_plan_json_path_for_state(state),
                **_planning_artifact_fields(state, repaired_plan, repaired_path),
                "clarification": (
                    _project_plan_dependency_error_payload(remaining_errors)
                    if remaining_errors
                    else _planning_confirmation_payload(state, repaired_plan)
                ),
                "timeline": [phase],
            }
        # 完整重写 Markdown，确保手动篡改的数据源类型和实现边界也被恢复。
        project_plan_path = write_project_plan_document(state, project_plan)
        return {
            "phase": phase,
            "status": "completed",
            "project_plan": project_plan,
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            **_planning_artifact_fields(state, project_plan, project_plan_path),
            "clarification": _planning_confirmed_payload(state, project_plan),
            "timeline": [phase],
        }

    requirement_spec = state["requirement_spec"]
    requirement_spec = _technical_planning_requirement_spec(state, requirement_spec)
    if existing_plan and request:
        requirement_spec = {
            **requirement_spec,
            "planning_adjustment_request": request,
        }
    if phase == "technical_planning":
        project_plan, validation_errors = _generate_valid_technical_plan(
            state,
            requirement_spec,
            existing_plan if isinstance(existing_plan, dict) else None,
        )
        if project_plan is None:
            return {
                "phase": phase,
                "status": "requires_user_input",
                "clarification": _technical_plan_generation_error_payload(validation_errors),
                "timeline": [phase],
            }
    else:
        project_plan = plan_project_with_chat_model(
            requirement_spec,
            **({"existing_plan": existing_plan} if existing_plan else {}),
            on_token=_planning_token_callback,
        )
        project_plan = apply_project_plan_feedback(
            project_plan,
            request,
        )
        project_plan = _attach_technical_plan_contracts(state, project_plan)
        project_plan["confirmation_status"] = "pending_user_confirmation"
        validation_errors = _project_plan_validation_errors(
            project_plan,
            state,
        )
        if validation_errors:
            project_plan, validation_errors = _repair_project_plan_validation_errors(
                project_plan,
                validation_errors,
                state=state,
                repair_attempts=1,
            )
    project_plan_path = write_project_plan_document(state, project_plan)
    clarification = (
        _project_plan_dependency_error_payload(validation_errors)
        if validation_errors
        else _planning_confirmation_payload(state, project_plan)
    )

    return {
        "phase": phase,
        "status": "requires_user_input",
        "project_plan": project_plan,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": _project_plan_json_path_for_state(state),
        **_planning_artifact_fields(state, project_plan, project_plan_path),
        "clarification": clarification,
        "timeline": [phase],
    }


def detail_confirmation(state: ProjectState) -> dict:
    """按页面批量确认缺失接口详情，并保留显式接口/实体独立详设。"""

    pending_plan = state.get("pending_project_plan")
    submission = state.get("detail_review_submission")
    selectedPageId = str(state.get("selectedPageId") or "")
    selected_api_contract_id = str(state.get("selected_api_contract_id") or "")
    selected_endpoint_id = str(state.get("selected_endpoint_id") or "")
    selected_entity_id = str(state.get("selected_entity_id") or "")
    detail_target_type = str(
        state.get("detail_target_type")
        or (
            "entity"
            if selected_entity_id
            else "endpoint"
            if selected_endpoint_id
            else "page"
            if selectedPageId
            else ""
        )
    )
    selected_endpoint_state = {
        **({"selected_api_contract_id": selected_api_contract_id} if selected_api_contract_id else {}),
        **({"selected_endpoint_id": selected_endpoint_id} if selected_endpoint_id else {}),
        **({"detail_target_type": detail_target_type} if detail_target_type else {}),
    }
    selected_entity_state = {
        **({"selected_entity_id": selected_entity_id} if selected_entity_id else {}),
        **({"detail_target_type": detail_target_type} if detail_target_type else {}),
    }
    selection_mode = (
        "entity_review"
        if detail_target_type == "entity" or selected_entity_id
        else "endpoint_review"
        if detail_target_type == "endpoint" or selected_endpoint_id
        else "batch_review"
    )
    if selectedPageId and detail_target_type == "page" and not selected_entity_id:
        return _page_endpoint_confirmation(state, selectedPageId)
    acceptance_adjustment = state.get("acceptance_adjustment")
    acceptance_adjustment_type = (
        str(acceptance_adjustment.get("type") or "").strip()
        if isinstance(acceptance_adjustment, dict)
        else ""
    )
    has_detail_submission = isinstance(submission, dict) and bool(submission)
    if (
        acceptance_adjustment_type
        in {"page_design_change", "endpoint_change", "data_source_change"}
        and not has_detail_submission
    ):
        return _regenerate_acceptance_detail_plan(
            state,
            selectedPageId=selectedPageId,
            selected_api_contract_id=selected_api_contract_id,
            selected_endpoint_id=selected_endpoint_id,
            selected_entity_id=selected_entity_id,
            detail_target_type=detail_target_type,
            regenerate_endpoint_details=acceptance_adjustment_type == "endpoint_change",
        )
    if acceptance_adjustment_type == "project_plan_change" and not has_detail_submission:
        return _regenerate_acceptance_detail_plan(
            state,
            selectedPageId=selectedPageId,
            selected_api_contract_id=selected_api_contract_id,
            selected_endpoint_id=selected_endpoint_id,
            selected_entity_id=selected_entity_id,
            detail_target_type=detail_target_type,
            regenerate_endpoint_details=True,
        )
    # 单卡片一次性提交：把 submit_entity_design 动作转译为实体确认载荷，
    # 由下方既有确认门禁完成校验、确认与数据库操作执行。
    entity_submit_action = (
        state.get("entity_design_action")
        if isinstance(state.get("entity_design_action"), dict)
        and str(state.get("entity_design_action", {}).get("action") or "")
        == "submit_entity_design"
        and str(state.get("entity_design_action", {}).get("entity_id") or "")
        == selected_entity_id
        else None
    )
    if entity_submit_action and not submission:
        submit_base_plan = (
            pending_plan
            if isinstance(pending_plan, dict)
            else state.get("project_plan")
            if isinstance(state.get("project_plan"), dict)
            else None
        )
        if not isinstance(submit_base_plan, dict):
            raise ValueError("缺少项目计划，无法提交实体设计。")
        review_plan = _ensure_entity_detail_for_submit(
            state,
            submit_base_plan,
            selected_entity_id,
            entity_submit_action,
        )
        entity_detail = next(
            (
                item
                for item in review_plan.get("entity_detail_plans", [])
                if isinstance(item, dict)
                and str(item.get("entity_id") or "") == selected_entity_id
            ),
            None,
        )
        if entity_detail is not None:
            apply_complete_entity_design(entity_detail, entity_submit_action)
            pending_plan = review_plan
            submission = {
                "review_status": "confirmed",
                "target_changes": [],
                "overall_note": "实体设计单卡片确认",
            }
    if pending_plan and isinstance(submission, dict):
        edited_markdown = edited_project_plan_markdown(state, pending_plan)
        synchronized_plan = (
            sync_project_plan_from_markdown(
                pending_plan,
                state.get("requirement_spec", {}),
                edited_markdown,
            )
            if edited_markdown is not None and state.get("requirement_spec")
            else pending_plan
        )
        if selected_entity_id:
            entity_errors = _pending_entity_design_validation_errors(
                synchronized_plan,
                selected_entity_id,
            )
            if entity_errors:
                review_plan = _attach_entity_design_validation_errors(
                    synchronized_plan,
                    selected_entity_id,
                    entity_errors,
                )
                return _entity_design_requires_revision(
                    state,
                    review_plan,
                    selected_entity_id=selected_entity_id,
                    detail_target_type=detail_target_type or "entity",
                )
        confirmed_plan = apply_detail_review_submission(
            synchronized_plan,
            submission,
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            selected_entity_id=selected_entity_id or None,
        )
        if selected_entity_id:
            confirmed_plan = _execute_confirmed_entity_database_operations(
                state,
                confirmed_plan,
                selected_entity_id,
            )
        project_plan_path = write_project_plan_document(state, confirmed_plan)
        return {
            "phase": "detail_confirmation",
            "status": "completed",
            "project_plan": confirmed_plan,
            "pending_project_plan": {},
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "clarification": (
                _entity_design_confirmed_payload(
                    confirmed_plan,
                    selected_entity_id=selected_entity_id,
                    detail_target_type=detail_target_type or "entity",
                )
                if selected_entity_id
                else _project_plan_confirmed_payload(confirmed_plan)
            ),
            "detail_selection": {
                "status": "completed",
                "mode": "batch_review",
                "targets": [],
            },
            "selectedPageId": selectedPageId or None,
            **selected_endpoint_state,
            **selected_entity_state,
            "detail_plans": [
                *confirmed_plan.get("page_detail_plans", []),
                *confirmed_plan.get("endpoint_detail_plans", []),
                *confirmed_plan.get("entity_detail_plans", []),
            ],
            "detail_review_submission": {},
            "acceptance_adjustment": {},
            "timeline": ["detail_confirmation"],
        }

    if pending_plan and _user_confirmed_project_plan(state.get("request", "")):
        legacy_submission = {
            "review_status": "confirmed",
            "target_changes": [],
            "overall_note": "legacy text confirmation",
        }
        return detail_confirmation(
            {**state, "detail_review_submission": legacy_submission}
        )

    if pending_plan and (selectedPageId or selected_endpoint_id or selected_entity_id):
        review_plan = pending_plan
        project_plan_path = state.get("project_plan_path")
        ai_suggestions: dict[str, Any] | None = None
        ddl_execution: dict[str, Any] | None = None
        if selectedPageId and not _has_selected_page_detail(review_plan, selectedPageId):
            # 旧会话可能保留上一页面的待确认计划；新选择的页面缺失时必须基于最新正式计划补生成。
            source_plan = state.get("project_plan")
            if not isinstance(source_plan, dict):
                source_plan = pending_plan
            try:
                review_plan = _generate_all_detail_plans(
                    source_plan,
                    frontend_pages=state.get("pages") or state.get("frontend_pages"),
                    selectedPageId=selectedPageId,
                    enforce_entity_gate=True,
                    **_detail_workspace_options(state),
                )
            except PageDependencyGapError as exc:
                return {
                    "phase": "detail_confirmation",
                    "status": "requires_user_input",
                    "project_plan": source_plan,
                    "clarification": _project_plan_revision_required_payload(str(exc)),
                    "selectedPageId": selectedPageId,
                    "timeline": ["detail_confirmation"],
                }
            except EntityDesignRequiredError as exc:
                return {
                    "phase": "detail_confirmation",
                    "status": "requires_user_input",
                    "project_plan": source_plan,
                    "clarification": _entity_design_required_payload(exc),
                    "selectedPageId": selectedPageId,
                    "timeline": ["detail_confirmation"],
                }
            review_plan["confirmation_status"] = "pending_user_confirmation"
            project_plan_path = write_project_plan_document(state, review_plan)
        if selected_endpoint_id and not _has_selected_endpoint_detail(
            review_plan,
            selected_api_contract_id,
            selected_endpoint_id,
        ):
            source_plan = state.get("project_plan")
            if not isinstance(source_plan, dict):
                source_plan = pending_plan
            try:
                review_plan = _generate_all_detail_plans(
                    source_plan,
                    selected_api_contract_id=selected_api_contract_id,
                    selected_endpoint_id=selected_endpoint_id,
                    detail_target_type=detail_target_type or "endpoint",
                    enforce_entity_gate=True,
                    **_detail_workspace_options(state),
                )
            except EntityDesignRequiredError as exc:
                return {
                    "phase": "detail_confirmation",
                    "status": "requires_user_input",
                    "project_plan": source_plan,
                    "clarification": _entity_design_required_payload(exc),
                    "selected_api_contract_id": selected_api_contract_id,
                    "selected_endpoint_id": selected_endpoint_id,
                    "detail_target_type": detail_target_type or "endpoint",
                    "timeline": ["detail_confirmation"],
                }
            review_plan["confirmation_status"] = "pending_user_confirmation"
            project_plan_path = write_project_plan_document(state, review_plan)
        if selected_entity_id:
            source_plan = state.get("project_plan")
            if not isinstance(source_plan, dict):
                source_plan = review_plan
            action = state.get("entity_design_action")
            if isinstance(action, dict) and str(action.get("entity_id") or "") == selected_entity_id:
                if str(action.get("action") or "") == "ai_assist":
                    ai_suggestions = _build_entity_design_ai_suggestions(
                        source_plan,
                        selected_entity_id,
                        action,
                        workspace_root=_detail_workspace_options(state).get(
                            "workspace_root"
                        ),
                    )
                elif str(action.get("action") or "") == "execute_add_columns":
                    ddl_execution = _execute_entity_add_columns_with_agent(
                        state,
                        source_plan,
                        selected_entity_id,
                        action,
                    )
                    if ddl_execution.get("status") == "approval_required":
                        return {
                            "phase": "detail_confirmation",
                            "status": "requires_user_input",
                            "entity_design_action": {
                                **action,
                                "database_change_plan": ddl_execution.get(
                                    "database_change_plan"
                                ),
                            },
                            "clarification": _entity_ddl_approval_payload(
                                ddl_execution
                            ),
                            "pending_project_plan": review_plan,
                            "project_plan": state.get("project_plan"),
                            "project_plan_path": project_plan_path,
                            "project_plan_json_path": _project_plan_json_path_for_state(
                                state
                            ),
                            "timeline": ["detail_confirmation"],
                        }
                elif str(action.get("action") or "") == "execute_create_table":
                    ddl_execution = _execute_entity_create_table_action(
                        state,
                        source_plan,
                        selected_entity_id,
                        action,
                    )
                    if ddl_execution.get("status") == "approval_required":
                        return {
                            "phase": "detail_confirmation",
                            "status": "requires_user_input",
                            "entity_design_action": {
                                **action,
                                "database_change_plan": ddl_execution.get(
                                    "database_change_plan"
                                ),
                            },
                            "clarification": _entity_ddl_approval_payload(
                                ddl_execution
                            ),
                            "pending_project_plan": review_plan,
                            "project_plan": state.get("project_plan"),
                            "project_plan_path": project_plan_path,
                            "project_plan_json_path": _project_plan_json_path_for_state(
                                state
                            ),
                            "timeline": ["detail_confirmation"],
                        }
                else:
                    review_plan = _apply_entity_design_action(
                        state,
                        source_plan,
                        review_plan,
                        selected_entity_id,
                    )
                    review_plan["confirmation_status"] = "pending_user_confirmation"
                    project_plan_path = write_project_plan_document(state, review_plan)
        clarification = detail_review_payload(
            review_plan,
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            selected_entity_id=selected_entity_id or None,
            detail_target_type=detail_target_type or None,
        )
        if ai_suggestions is not None:
            entity_design = (
                clarification.get("review", {}).get("summary", {}).get("entityDesign")
            )
            if isinstance(entity_design, dict):
                entity_design["ai_suggestions"] = ai_suggestions
        if ddl_execution is not None:
            entity_design = (
                clarification.get("review", {}).get("summary", {}).get("entityDesign")
            )
            if isinstance(entity_design, dict):
                entity_design["ddl_execution"] = ddl_execution
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": clarification,
            "pending_project_plan": review_plan,
            "project_plan": state.get("project_plan"),
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "detail_selection": {
                "status": "requires_user_input",
                "mode": selection_mode,
                "selectedPageId": selectedPageId or None,
                **selected_endpoint_state,
                **selected_entity_state,
                "targets": _selected_detail_design_targets(
                    review_plan,
                    selectedPageId,
                    selected_api_contract_id=selected_api_contract_id or None,
                    selected_endpoint_id=selected_endpoint_id or None,
                    selected_entity_id=selected_entity_id or None,
                ),
            },
            "selectedPageId": selectedPageId or None,
            **selected_endpoint_state,
            **selected_entity_state,
            "detail_plans": _selected_detail_plans(
                review_plan,
                selectedPageId,
                selected_api_contract_id=selected_api_contract_id or None,
                selected_endpoint_id=selected_endpoint_id or None,
                selected_entity_id=selected_entity_id or None,
            ),
            "entity_design_action": {},
            "timeline": ["detail_confirmation"],
        }

    if pending_plan:
        revised_plan = revise_project_plan_with_chat_model(
            pending_plan,
            state.get("request", ""),
            on_token=_planning_token_callback,
        )
        revised_plan = _generate_all_detail_plans(
            revised_plan,
            **_detail_workspace_options(state),
        )
        revised_plan["confirmation_status"] = "pending_user_confirmation"
        project_plan_path = write_project_plan_document(state, revised_plan)
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": detail_review_payload(revised_plan),
            "pending_project_plan": revised_plan,
            "project_plan": state.get("project_plan"),
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "detail_selection": {
                "status": "requires_user_input",
                "mode": "batch_review",
                "targets": detail_design_targets(revised_plan),
            },
            "timeline": ["detail_confirmation"],
        }

    project_plan = state.get("project_plan")
    if not isinstance(project_plan, dict):
        raise ValueError(
            "主 Workflow 需要工作区 .xcodeagent/plans/project-plan.json "
            "（兼容 plans/project-plan.json）作为初始输入。"
        )
    if not selectedPageId and not selected_endpoint_id and not selected_entity_id:
        raise ValueError(
            "开始详细设计时必须提供 selectedPageId、selectedEndpointId 或 selectedEntityId。"
        )
    if selected_entity_id and not _has_selected_entity_detail(
        project_plan,
        selected_entity_id,
    ):
        # 实体设计从数据源选择开始：首次进入不自动生成详情，而是返回选择界面。
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": detail_review_payload(
                project_plan,
                selected_entity_id=selected_entity_id,
                detail_target_type=detail_target_type or "entity",
            ),
            "pending_project_plan": project_plan,
            "project_plan": project_plan,
            "project_plan_path": state.get("project_plan_path"),
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "detail_selection": {
                "status": "requires_user_input",
                "mode": "entity_review",
                **selected_entity_state,
                "targets": [],
            },
            **selected_entity_state,
            "detail_plans": [],
            "timeline": ["detail_confirmation"],
        }
    if selected_entity_id and _has_selected_entity_detail(
        project_plan,
        selected_entity_id,
    ):
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": detail_review_payload(
                project_plan,
                selected_entity_id=selected_entity_id,
                detail_target_type=detail_target_type or "entity",
            ),
            "pending_project_plan": project_plan,
            "project_plan": project_plan,
            "project_plan_path": state.get("project_plan_path"),
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "detail_selection": {
                "status": "requires_user_input",
                "mode": "entity_review",
                **selected_entity_state,
                "targets": _selected_detail_design_targets(
                    project_plan,
                    "",
                    selected_entity_id=selected_entity_id,
                ),
            },
            **selected_entity_state,
            "detail_plans": _selected_detail_plans(
                project_plan,
                "",
                selected_entity_id=selected_entity_id,
            ),
            "timeline": ["detail_confirmation"],
        }
    if (
        selectedPageId
        and _has_selected_page_detail(project_plan, selectedPageId)
        and _page_endpoint_details_complete(project_plan, selectedPageId)
    ):
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": detail_review_payload(
                project_plan,
                selectedPageId=selectedPageId,
                selected_api_contract_id=selected_api_contract_id or None,
                selected_endpoint_id=selected_endpoint_id or None,
                detail_target_type=detail_target_type or None,
            ),
            "pending_project_plan": project_plan,
            "project_plan": project_plan,
            "project_plan_path": state.get("project_plan_path"),
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "detail_selection": {
                "status": "requires_user_input",
                "mode": "batch_review",
                "selectedPageId": selectedPageId,
                **selected_endpoint_state,
                "targets": _selected_detail_design_targets(
                    project_plan,
                    selectedPageId,
                ),
            },
            "selectedPageId": selectedPageId,
            **selected_endpoint_state,
            "detail_plans": _selected_detail_plans(project_plan, selectedPageId),
            "timeline": ["detail_confirmation"],
        }
    if selected_endpoint_id and _has_selected_endpoint_detail(
        project_plan,
        selected_api_contract_id,
        selected_endpoint_id,
    ):
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": detail_review_payload(
                project_plan,
                selected_api_contract_id=selected_api_contract_id or None,
                selected_endpoint_id=selected_endpoint_id,
                detail_target_type=detail_target_type or "endpoint",
            ),
            "pending_project_plan": project_plan,
            "project_plan": project_plan,
            "project_plan_path": state.get("project_plan_path"),
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "detail_selection": {
                "status": "requires_user_input",
                "mode": "endpoint_review",
                **selected_endpoint_state,
                "targets": _selected_detail_design_targets(
                    project_plan,
                    "",
                    selected_api_contract_id=selected_api_contract_id or None,
                    selected_endpoint_id=selected_endpoint_id,
                ),
            },
            **selected_endpoint_state,
            "detail_plans": _selected_detail_plans(
                project_plan,
                "",
                selected_api_contract_id=selected_api_contract_id or None,
                selected_endpoint_id=selected_endpoint_id,
            ),
            "timeline": ["detail_confirmation"],
        }
    try:
        project_plan_pages = project_plan_page_records(project_plan)
        frontend_pages = project_plan_pages or state.get("pages") or state.get("frontend_pages")
        pending_plan = _generate_all_detail_plans(
            project_plan,
            frontend_pages=frontend_pages,
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            selected_entity_id=selected_entity_id or None,
            detail_target_type=detail_target_type or None,
            enforce_entity_gate=bool(selectedPageId or selected_endpoint_id),
            **_detail_workspace_options(state),
        )
    except EntityDesignRequiredError as exc:
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "clarification": _entity_design_required_payload(exc),
            "selectedPageId": selectedPageId or None,
            "selected_api_contract_id": selected_api_contract_id or None,
            "selected_endpoint_id": selected_endpoint_id or None,
            "detail_target_type": detail_target_type or None,
            "timeline": ["detail_confirmation"],
        }
    except (PageDependencyGapError, ValueError) as exc:
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "clarification": _project_plan_revision_required_payload(str(exc)),
            "timeline": ["detail_confirmation"],
        }
    pending_plan["confirmation_status"] = "pending_user_confirmation"
    project_plan_path = write_project_plan_document(state, pending_plan)
    targets = _selected_detail_design_targets(
        pending_plan,
        selectedPageId,
        selected_api_contract_id=selected_api_contract_id or None,
        selected_endpoint_id=selected_endpoint_id or None,
        selected_entity_id=selected_entity_id or None,
    )
    return {
        "phase": "detail_confirmation",
        "status": "requires_user_input",
        "clarification": detail_review_payload(
            pending_plan,
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            selected_entity_id=selected_entity_id or None,
            detail_target_type=detail_target_type or None,
        ),
        "pending_project_plan": pending_plan,
        "project_plan": project_plan,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": _project_plan_json_path_for_state(state),
        "detail_selection": {
            "status": "requires_user_input",
            "mode": selection_mode,
            "selectedPageId": selectedPageId or None,
            **selected_endpoint_state,
            **selected_entity_state,
            "targets": targets,
        },
        "selectedPageId": selectedPageId or None,
        **selected_endpoint_state,
        **selected_entity_state,
        "detail_plans": _selected_detail_plans(
            pending_plan,
            selectedPageId,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            selected_entity_id=selected_entity_id or None,
        ),
        "timeline": ["detail_confirmation"],
    }


def _page_endpoint_confirmation(state: ProjectState, selected_page_id: str) -> dict:
    """按页面实现契约批量生成并确认缺失接口详情，不再生成 PageDetail。"""

    pending_plan = state.get("pending_project_plan")
    submission = state.get("detail_review_submission")
    if isinstance(pending_plan, dict) and isinstance(submission, dict) and submission:
        confirmed_plan = apply_detail_review_submission(
            pending_plan,
            submission,
            selectedPageId=selected_page_id,
        )
        project_plan_path = write_project_plan_document(state, confirmed_plan)
        return {
            "phase": "detail_confirmation",
            "status": "completed",
            "project_plan": confirmed_plan,
            "pending_project_plan": {},
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "clarification": _page_contract_ready_payload(selected_page_id),
            "detail_selection": {"status": "completed", "mode": "endpoint_batch", "targets": []},
            "selectedPageId": selected_page_id,
            "detail_target_type": "page",
            "detail_plans": list(confirmed_plan.get("endpoint_detail_plans") or []),
            "detail_review_submission": {},
            "acceptance_adjustment": {},
            "timeline": ["detail_confirmation"],
        }

    detail_selection = state.get("detail_selection")
    pending_selected_page_id = (
        str(detail_selection.get("selectedPageId") or "")
        if isinstance(detail_selection, dict)
        else ""
    )
    if isinstance(pending_plan, dict) and pending_selected_page_id == selected_page_id:
        return _page_endpoint_review_result(
            state,
            pending_plan,
            selected_page_id,
            state.get("project_plan_path"),
        )

    project_plan = state.get("project_plan")
    if not isinstance(project_plan, dict):
        raise ValueError("页面开发需要已确认的 TechnicalPlan。")
    try:
        generated_plan = _generate_all_detail_plans(
            project_plan,
            frontend_pages=project_plan_page_records(project_plan),
            selectedPageId=selected_page_id,
            detail_target_type="page",
            enforce_entity_gate=True,
            **_detail_workspace_options(state),
        )
    except EntityDesignRequiredError as exc:
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "clarification": _entity_design_required_payload(exc),
            "selectedPageId": selected_page_id,
            "timeline": ["detail_confirmation"],
        }
    except (PageDependencyGapError, ValueError) as exc:
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "clarification": _project_plan_revision_required_payload(str(exc)),
            "selectedPageId": selected_page_id,
            "timeline": ["detail_confirmation"],
        }
    pending_details = list(generated_plan.get("endpoint_detail_plans") or [])
    if not pending_details:
        return {
            "phase": "detail_confirmation",
            "status": "completed",
            "project_plan": project_plan,
            "pending_project_plan": {},
            "project_plan_path": state.get("project_plan_path"),
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "clarification": _page_contract_ready_payload(selected_page_id),
            "detail_selection": {"status": "completed", "mode": "page_contract", "targets": []},
            "selectedPageId": selected_page_id,
            "detail_target_type": "page",
            "detail_plans": [],
            "timeline": ["detail_confirmation"],
        }
    generated_plan["confirmation_status"] = "pending_user_confirmation"
    project_plan_path = write_project_plan_document(state, generated_plan)
    return _page_endpoint_review_result(
        state,
        generated_plan,
        selected_page_id,
        project_plan_path,
    )


def _page_endpoint_review_result(
    state: ProjectState,
    pending_plan: dict,
    selected_page_id: str,
    project_plan_path: str | None,
) -> dict:
    """构造页面依赖接口的批量确认结果。"""

    return {
        "phase": "detail_confirmation",
        "status": "requires_user_input",
        "clarification": detail_review_payload(
            pending_plan,
            selectedPageId=selected_page_id,
            detail_target_type="endpoint_batch",
        ),
        "pending_project_plan": pending_plan,
        "project_plan": state.get("project_plan"),
        "project_plan_path": project_plan_path,
        "project_plan_json_path": _project_plan_json_path_for_state(state),
        "detail_selection": {
            "status": "requires_user_input",
            "mode": "endpoint_batch",
            "selectedPageId": selected_page_id,
            "targets": [
                {
                    "id": f"{detail.get('api_contract_id')}:{detail.get('endpoint_id')}",
                    "type": "endpoint",
                    "label": f"接口：{detail.get('method')} {detail.get('path')}",
                    "name": detail.get("name") or detail.get("endpoint_id"),
                }
                for detail in pending_plan.get("endpoint_detail_plans", [])
                if isinstance(detail, dict)
            ],
        },
        "selectedPageId": selected_page_id,
        "detail_target_type": "page",
        "detail_plans": list(pending_plan.get("endpoint_detail_plans") or []),
        "timeline": ["detail_confirmation"],
    }


def _page_contract_ready_payload(selected_page_id: str) -> dict:
    """说明页面实现契约及接口详情已满足开发前置条件。"""

    return {
        "mode": "page_implementation_ready",
        "status": "clear",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": [],
        "message": f"页面 `{selected_page_id}` 的实现契约和接口详情已就绪，可以进入开发。",
    }


def _regenerate_acceptance_detail_plan(
    state: ProjectState,
    *,
    selectedPageId: str,
    selected_api_contract_id: str,
    selected_endpoint_id: str,
    selected_entity_id: str,
    detail_target_type: str,
    regenerate_endpoint_details: bool,
) -> dict:
    """根据验收反馈生成新的页面、接口或实体详情版本，并重新停在确认门禁。"""

    project_plan = state.get("project_plan")
    adjustment = state.get("acceptance_adjustment")
    feedback = (
        str(adjustment.get("feedback") or "").strip()
        if isinstance(adjustment, dict)
        else ""
    )
    if not isinstance(project_plan, dict):
        raise ValueError("验收调整缺少当前已确认的 ProjectPlan。")
    if not selectedPageId and not selected_endpoint_id and not selected_entity_id:
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "clarification": _project_plan_revision_required_payload(
                "验收调整缺少当前页面、接口或实体目标。"
            ),
            "timeline": ["detail_confirmation"],
        }

    try:
        pending_plan = _generate_all_detail_plans(
            project_plan,
            frontend_pages=state.get("pages") or state.get("frontend_pages"),
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            selected_entity_id=selected_entity_id or None,
            detail_target_type=detail_target_type or None,
            workspace_root=_detail_workspace_options(state).get("workspace_root"),
            user_request=feedback,
            regenerate_endpoint_details=regenerate_endpoint_details,
        )
    except PageDependencyGapError as exc:
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "clarification": _project_plan_revision_required_payload(str(exc)),
            "selectedPageId": selectedPageId or None,
            "selected_api_contract_id": selected_api_contract_id or None,
            "selected_endpoint_id": selected_endpoint_id or None,
            "selected_entity_id": selected_entity_id or None,
            "detail_target_type": detail_target_type or None,
            "timeline": ["detail_confirmation"],
        }

    pending_plan["confirmation_status"] = "pending_user_confirmation"
    project_plan_path = write_project_plan_document(state, pending_plan)
    targets = _selected_detail_design_targets(
        pending_plan,
        selectedPageId,
        selected_api_contract_id=selected_api_contract_id or None,
        selected_endpoint_id=selected_endpoint_id or None,
        selected_entity_id=selected_entity_id or None,
    )
    selected_endpoint_state = {
        **(
            {"selected_api_contract_id": selected_api_contract_id}
            if selected_api_contract_id
            else {}
        ),
        **(
            {"selected_endpoint_id": selected_endpoint_id}
            if selected_endpoint_id
            else {}
        ),
        **({"detail_target_type": detail_target_type} if detail_target_type else {}),
    }
    selected_entity_state = {
        **({"selected_entity_id": selected_entity_id} if selected_entity_id else {}),
        **({"detail_target_type": detail_target_type} if detail_target_type else {}),
    }
    selection_mode = (
        "entity_review"
        if detail_target_type == "entity" or selected_entity_id
        else "endpoint_review"
        if detail_target_type == "endpoint" or selected_endpoint_id
        else "batch_review"
    )
    return {
        "phase": "detail_confirmation",
        "status": "requires_user_input",
        "clarification": detail_review_payload(
            pending_plan,
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            selected_entity_id=selected_entity_id or None,
            detail_target_type=detail_target_type or None,
        ),
        "pending_project_plan": pending_plan,
        "project_plan": project_plan,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": _project_plan_json_path_for_state(state),
        "detail_selection": {
            "status": "requires_user_input",
            "mode": selection_mode,
            "selectedPageId": selectedPageId or None,
            **selected_endpoint_state,
            **selected_entity_state,
            "targets": targets,
        },
        "selectedPageId": selectedPageId or None,
        **selected_endpoint_state,
        **selected_entity_state,
        "detail_plans": _selected_detail_plans(
            pending_plan,
            selectedPageId,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            selected_entity_id=selected_entity_id or None,
        ),
        "timeline": ["detail_confirmation"],
    }


def _generate_all_detail_plans(
    project_plan: dict,
    *,
    frontend_pages: list[dict] | None = None,
    selectedPageId: str | None = None,
    selected_api_contract_id: str | None = None,
    selected_endpoint_id: str | None = None,
    selected_entity_id: str | None = None,
    detail_target_type: str | None = None,
    workspace_root: str | None = None,
    user_request: str = "",
    regenerate_endpoint_details: bool = False,
    enforce_entity_gate: bool = False,
) -> dict:
    """为页面依赖接口、显式 endpoint 或实体生成对应详细设计。"""

    project_pages = project_plan_page_records(project_plan)
    normalized_project_page_leaves = [
        _normalize_detail_page(page)
        for page in project_pages
        if isinstance(page, dict)
    ]
    normalized_project_pages = update_frontend_page_leaves(
        project_pages,
        {
            str(page.get("pageId") or page.get("id") or "").strip(): page
            for page in normalized_project_page_leaves
            if str(page.get("pageId") or page.get("id") or "").strip()
        },
    )
    page_field = "pages" if project_plan.get("artifact_type") == "technical-plan" else "frontend_pages"
    updated_plan = (
        project_plan
        if normalized_project_pages == project_pages
        else {**project_plan, page_field: normalized_project_pages}
    )
    source_pages = (
        frontend_pages
        if isinstance(frontend_pages, list)
        else normalized_project_page_leaves
    )
    pages = [
        _normalize_detail_page(page)
        for page in source_pages
        if isinstance(page, dict)
    ]
    if selectedPageId:
        pages = [
            page
            for page in pages
            if page.get("pageId") == selectedPageId
        ]
        if not pages:
            raise ValueError(f"项目计划中不存在页面：{selectedPageId}")
        # 新流程不生成页面正文；已有 EndpointDetail 只用于判断当前页面的接口缺口。
        updated_plan = {
            **updated_plan,
            "page_detail_plans": [],
        }
        _drop_legacy_detail_fields(updated_plan)
    if detail_target_type == "endpoint" or selected_endpoint_id:
        if not selected_api_contract_id or not selected_endpoint_id:
            raise ValueError("接口详细设计必须提供 selectedApiContractId 和 selectedEndpointId。")
        updated_plan, detail = _generate_endpoint_detail_plan(
            updated_plan,
            selected_api_contract_id,
            selected_endpoint_id,
            workspace_root,
            user_request=user_request,
            enforce_entity_gate=True,
        )
        updated_plan = {
            **updated_plan,
            "page_detail_plans": [],
            "endpoint_detail_plans": [detail],
        }
        _drop_legacy_detail_fields(updated_plan)
        updated_plan["detail_confirmation_summary"] = {
            "confirmed_pages": 0,
            "confirmed_endpoints": 0,
            "total_pages": 0,
            "total_endpoints": 1,
            "mode": "endpoint_review",
        }
        return updated_plan
    if detail_target_type == "entity" or selected_entity_id:
        if not selected_entity_id:
            raise ValueError("实体详细设计必须提供 selectedEntityId。")
        updated_plan, detail = _generate_entity_detail_plan(
            updated_plan,
            selected_entity_id,
            user_request=user_request,
        )
        updated_plan = {
            **updated_plan,
            "page_detail_plans": [],
            "endpoint_detail_plans": [],
            "entity_detail_plans": [detail],
        }
        _drop_legacy_detail_fields(updated_plan)
        updated_plan["entities"] = [
            {
                **entity,
                "detail_status": "pending_user_confirmation",
            }
            if isinstance(entity, dict)
            and str(entity.get("id") or "") == selected_entity_id
            else entity
            for entity in updated_plan.get("entities", [])
        ]
        updated_plan["detail_confirmation_summary"] = {
            "confirmed_pages": 0,
            "confirmed_endpoints": 0,
            "confirmed_entities": 0,
            "total_pages": 0,
            "total_endpoints": 0,
            "total_entities": 1,
            "mode": "entity_review",
        }
        return updated_plan

    endpoint_review_details: list[dict] = []
    endpoint_review_keys: set[tuple[str, str]] = set()
    for page in pages:
        pageId = page.get("pageId") if isinstance(page, dict) else None
        if not pageId:
            continue
        references = extract_page_detail_context(updated_plan, pageId).get("references", {})
        for dependency in references.get("endpoint_dependencies", []):
            if not isinstance(dependency, dict):
                continue
            api_contract_id, endpoint_id = _resolve_endpoint_dependency(
                updated_plan,
                dependency,
            )
            detail_key = (api_contract_id, endpoint_id)
            if detail_key in endpoint_review_keys:
                continue
            existing_detail = _find_formal_endpoint_detail(
                updated_plan,
                api_contract_id,
                endpoint_id,
            )
            if existing_detail is None or regenerate_endpoint_details:
                updated_plan, existing_detail = _generate_endpoint_detail_plan(
                    updated_plan,
                    api_contract_id,
                    endpoint_id,
                    workspace_root,
                    user_request=user_request,
                    enforce_entity_gate=enforce_entity_gate,
                )
            if str(existing_detail.get("status") or "") != "confirmed":
                endpoint_review_details.append(existing_detail)
                endpoint_review_keys.add(detail_key)

    # 页面选择只携带需要共同确认的 EndpointDetail；视觉和产品语义由上游正式产物负责。
    updated_plan["endpoint_detail_plans"] = endpoint_review_details

    updated_plan["detail_confirmation_summary"] = {
        "confirmed_pages": 0,
        "confirmed_endpoints": 0,
        "total_pages": 0,
        "total_endpoints": len(endpoint_review_details),
        "mode": "batch_review",
    }
    return updated_plan


def _resolve_endpoint_dependency(
    project_plan: dict,
    dependency: dict,
) -> tuple[str, str]:
    """把页面 endpoint 引用解析为唯一的契约与接口标识。"""

    endpoint_id = str(dependency.get("endpoint_id") or "").strip()
    requested_contract_id = str(dependency.get("api_contract_id") or "").strip()
    matches: list[str] = []
    for contract in project_plan.get("api_contracts", []):
        if not isinstance(contract, dict):
            continue
        contract_id = str(contract.get("id") or "")
        if requested_contract_id and contract_id != requested_contract_id:
            continue
        if any(
            isinstance(endpoint, dict) and str(endpoint.get("id") or "") == endpoint_id
            for endpoint in contract.get("endpoints", []) or []
        ):
            matches.append(contract_id)
    if len(matches) != 1:
        raise ValueError(f"页面依赖无法唯一定位接口：{requested_contract_id}:{endpoint_id}")
    return matches[0], endpoint_id


def _find_formal_endpoint_detail(
    project_plan: dict,
    api_contract_id: str,
    endpoint_id: str,
) -> dict | None:
    """查找已存在且内容完整的 EndpointDetail，避免页面设计重复生成。"""

    return next(
        (
            detail
            for detail in project_plan.get("endpoint_detail_plans", [])
            if isinstance(detail, dict)
            and str(detail.get("api_contract_id") or "") == api_contract_id
            and str(detail.get("endpoint_id") or "") == endpoint_id
            and _has_formal_endpoint_detail_content(detail)
        ),
        None,
    )


def _generate_endpoint_detail_plan(
    project_plan: dict,
    api_contract_id: str,
    endpoint_id: str,
    workspace_root: str | None = None,
    user_request: str = "",
    enforce_entity_gate: bool = False,
) -> tuple[dict, dict]:
    """复用独立 endpoint 设计链路生成详情并挂回 ProjectPlan 内存态。"""

    _detail_progress(
        "开始生成接口详细设计。",
        target_type="endpoint",
        api_contract_id=api_contract_id,
        endpoint_id=endpoint_id,
    )
    if enforce_entity_gate:
        gate_errors, missing_entities = entity_bound_design_gate(
            project_plan,
            api_contract_id,
        )
        if gate_errors:
            raise EntityDesignRequiredError(
                "；".join(gate_errors),
                missing_entities=missing_entities,
            )
    endpoint_context = extract_endpoint_detail_context(
        project_plan,
        api_contract_id,
        endpoint_id,
    )
    _detail_progress(
        "已定位接口契约，正在调用模型生成接口决策。",
        target_type="endpoint",
        api_contract_id=api_contract_id,
        endpoint_id=endpoint_id,
        method=endpoint_context.get("method"),
        path=endpoint_context.get("path"),
    )
    detail = design_endpoint_with_chat_model(
        project_plan,
        endpoint_context,
        user_request,
    )
    _detail_progress(
        "接口决策已闭合，完整接口详情已确定性组装。",
        target_type="endpoint",
        api_contract_id=api_contract_id,
        endpoint_id=endpoint_id,
        design_source=detail.get("design_source"),
    )
    detail["status"] = "pending_user_confirmation"
    detail["approved"] = False
    updated_plan = attach_endpoint_detail_plan(project_plan, detail)
    _detail_progress(
        "接口详细设计已挂回 ProjectPlan，等待用户确认。",
        target_type="endpoint",
        api_contract_id=api_contract_id,
        endpoint_id=endpoint_id,
        detail_plan_id=detail.get("id"),
    )
    return updated_plan, detail


def _generate_entity_detail_plan(
    project_plan: dict,
    entity_id: str,
    user_request: str = "",
) -> tuple[dict, dict]:
    """基于已确认实体定义生成实体详细设计；数据源默认值来自应用级边界。"""

    _detail_progress(
        "开始生成实体详细设计。",
        target_type="entity",
        entity_id=entity_id,
    )
    entity = next(
        (
            item
            for item in project_plan.get("entities", [])
            if isinstance(item, dict) and str(item.get("id") or "") == entity_id
        ),
        None,
    )
    if entity is None:
        raise ValueError(f"项目计划中不存在实体：{entity_id}")
    existing_detail = next(
        (
            item
            for item in project_plan.get("entity_detail_plans", [])
            if isinstance(item, dict) and str(item.get("entity_id") or "") == entity_id
        ),
        None,
    )
    detail = create_entity_detail_plan(
        project_plan,
        entity,
        user_request=user_request,
        default_datasource_type=(
            str(existing_detail.get("data_source_type") or "database")
            if isinstance(existing_detail, dict)
            else "database"
        ),
        design_stage=(
            existing_detail.get("design_stage")
            if isinstance(existing_detail, dict)
            else None
        ),
    )
    _detail_progress(
        "实体详细设计已确定性组装，等待用户确认。",
        target_type="entity",
        entity_id=entity_id,
        data_source_id=detail.get("data_source_id"),
        design_source=detail.get("design_source"),
    )
    detail["status"] = "pending_user_confirmation"
    detail["approved"] = False
    updated_plan = attach_entity_detail_plan(project_plan, detail)
    _detail_progress(
        "实体详细设计已挂回 ProjectPlan，等待用户确认。",
        target_type="entity",
        entity_id=entity_id,
        detail_plan_id=detail.get("id"),
    )
    return updated_plan, detail


def _ensure_entity_detail_for_submit(
    state: ProjectState,
    plan: dict[str, Any],
    entity_id: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    """单卡片提交前确保实体详情存在；不存在时按所选数据源创建。"""

    existing = next(
        (
            item
            for item in plan.get("entity_detail_plans", [])
            if isinstance(item, dict) and str(item.get("entity_id") or "") == entity_id
        ),
        None,
    )
    if existing is not None:
        return plan
    source_plan = (
        state.get("project_plan")
        if isinstance(state.get("project_plan"), dict)
        else plan
    )
    entity = next(
        (
            item
            for item in source_plan.get("entities", [])
            if isinstance(item, dict) and str(item.get("id") or "") == entity_id
        ),
        None,
    )
    if entity is None:
        raise ValueError(f"项目计划中不存在实体：{entity_id}")
    detail = create_entity_detail_plan(
        source_plan,
        entity,
        default_datasource_type=str(action.get("data_source_type") or ""),
        design_stage=ENTITY_DESIGN_STAGE_REVIEW_READY,
    )
    return attach_entity_detail_plan(plan, detail)


def _build_entity_design_ai_suggestions(
    source_plan: dict[str, Any],
    entity_id: str,
    action: dict[str, Any],
    *,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    """定位实体并生成 AI 辅助建议；实体缺失时返回可展示错误。"""

    entity = next(
        (
            item
            for item in source_plan.get("entities", [])
            if isinstance(item, dict) and str(item.get("id") or "") == entity_id
        ),
        None,
    )
    if entity is None:
        return {
            "assist_type": str(action.get("assist_type") or ""),
            "suggestions": [],
            "source": "error",
            "note": f"项目计划中不存在实体：{entity_id}",
        }
    context = dict(
        action.get("context") if isinstance(action.get("context"), dict) else {}
    )
    # 注入工作区路径，供后端按真实库表结构校验/过滤 AI 建议，前端无需改动。
    if workspace_root:
        context.setdefault("workspace_root", workspace_root)
    assist_type = str(action.get("assist_type") or "")
    if workspace_root and assist_type == "table_selection":
        try:
            # 表选型优先走数据库 Deep Agent：用 get_mysql_table_info 工具
            # 读取真实表与列后给出建议；失败时降级为普通模型调用。
            return entity_design_table_selection_with_agent(
                entity=entity,
                context=context,
                workspace=workspace_root,
                selected_skill_names=None,
            )
        except Exception:
            pass
    if workspace_root and assist_type == "bindings":
        try:
            # 字段映射同样走数据库 Deep Agent：指定表后读取真实列再绑定，
            # 与表选型复用同一个 Agent，避免模型臆造列名。
            return entity_design_bindings_with_agent(
                entity=entity,
                context=context,
                workspace=workspace_root,
                selected_skill_names=None,
            )
        except Exception:
            pass
    return entity_design_ai_suggestions(
        entity,
        assist_type=assist_type,
        instruction=str(action.get("instruction") or ""),
        context=context,
    )


def _apply_entity_design_action(
    state: ProjectState,
    source_plan: dict[str, Any],
    review_plan: dict[str, Any],
    entity_id: str,
) -> dict[str, Any]:
    """应用实体设计动作：选择数据源、补充外部 API 信息、构建静态数据或审批表生成。"""

    action = state.get("entity_design_action") or {}
    entity = next(
        (
            item
            for item in source_plan.get("entities", [])
            if isinstance(item, dict) and str(item.get("id") or "") == entity_id
        ),
        None,
    )
    if entity is None:
        raise ValueError(f"项目计划中不存在实体：{entity_id}")
    action_name = str(action.get("action") or "")
    existing = next(
        (
            item
            for item in review_plan.get("entity_detail_plans", [])
            if isinstance(item, dict) and str(item.get("entity_id") or "") == entity_id
        ),
        None,
    )
    if action_name == "select_data_source":
        if existing is not None:
            return review_plan
        source_type = str(action.get("data_source_type") or "")
        design_stage = (
            "database_design"
            if source_type == "database"
            else ENTITY_DESIGN_STAGE_EXTERNAL_API_INPUT
            if source_type == "external_api"
            else "static_design"
        )
        detail = create_entity_detail_plan(
            source_plan,
            entity,
            default_datasource_type=source_type,
            design_stage=design_stage,
        )
        detail["status"] = "pending_user_confirmation"
        detail["approved"] = False
        return attach_entity_detail_plan(review_plan, detail)
    if existing is None:
        raise ValueError("实体设计动作需要先选择数据源。")
    apply_entity_design_action(
        source_plan,
        existing,
        action,
        workspace_root=_detail_workspace_options(state).get("workspace_root"),
    )
    return attach_entity_detail_plan(review_plan, existing)


def _pending_entity_design_validation_errors(
    project_plan: dict[str, Any],
    entity_id: str,
) -> list[str]:
    """确认前校验实体设计是否符合 ProjectPlan 契约，返回可读错误列表。"""

    detail = next(
        (
            item
            for item in project_plan.get("entity_detail_plans", [])
            if isinstance(item, dict) and str(item.get("entity_id") or "") == entity_id
        ),
        None,
    )
    if detail is None:
        return [f"实体 {entity_id} 尚未生成实体设计。"]
    return entity_design_validation_errors(project_plan, detail)


def _attach_entity_design_validation_errors(
    project_plan: dict[str, Any],
    entity_id: str,
    errors: list[str],
) -> dict[str, Any]:
    """把确定性校验错误写回实体设计对应方案段落，供界面展示。"""

    updated = deepcopy(project_plan)
    for detail in updated.get("entity_detail_plans", []):
        if not isinstance(detail, dict) or str(detail.get("entity_id") or "") != entity_id:
            continue
        source_type = str(detail.get("data_source_type") or "")
        section_key = {
            "database": "database_design",
            "external_api": "external_api_design",
            "static": "static_design",
        }.get(source_type)
        if section_key:
            section = detail.get(section_key) if isinstance(detail.get(section_key), dict) else {}
            section["validation_errors"] = errors
            detail[section_key] = section
        detail["validation_errors"] = errors
    return updated


def _entity_design_requires_revision(
    state: ProjectState,
    review_plan: dict[str, Any],
    *,
    selected_entity_id: str,
    detail_target_type: str,
) -> dict:
    """实体设计校验失败时打回修订，继续停在实体设计确认门禁。"""

    selected_entity_state = {
        "selected_entity_id": selected_entity_id,
        "detail_target_type": detail_target_type,
    }
    return {
        "phase": "detail_confirmation",
        "status": "requires_user_input",
        "clarification": detail_review_payload(
            review_plan,
            selected_entity_id=selected_entity_id,
            detail_target_type=detail_target_type,
        ),
        "pending_project_plan": review_plan,
        "project_plan": state.get("project_plan"),
        "project_plan_path": state.get("project_plan_path"),
        "project_plan_json_path": _project_plan_json_path_for_state(state),
        "detail_selection": {
            "status": "requires_user_input",
            "mode": "entity_review",
            **selected_entity_state,
            "targets": _selected_detail_design_targets(
                review_plan,
                "",
                selected_entity_id=selected_entity_id,
            ),
        },
            **selected_entity_state,
            "detail_plans": _selected_detail_plans(
                review_plan,
                "",
                selected_entity_id=selected_entity_id,
            ),
            "detail_review_submission": {},
            "entity_design_action": {},
            "timeline": ["detail_confirmation"],
        }


def _execute_confirmed_entity_database_operations(
    state: ProjectState,
    project_plan: dict[str, Any],
    entity_id: str,
) -> dict[str, Any]:
    """实体设计确认后执行数据库表操作，并把执行证据写回实体详情。"""

    updated = deepcopy(project_plan)
    for detail in updated.get("entity_detail_plans", []):
        if not isinstance(detail, dict) or str(detail.get("entity_id") or "") != entity_id:
            continue
        if str(detail.get("status") or "") != "confirmed":
            continue
        if str(detail.get("data_source_type") or "") != "database":
            continue
        database_design = (
            detail.get("database_design")
            if isinstance(detail.get("database_design"), dict)
            else {}
        )
        operations = [
            item
            for item in database_design.get("database_operations", [])
            if isinstance(item, dict)
        ]
        if not operations:
            continue
        _detail_progress(
            "实体设计已确认，开始落地数据库表操作。",
            target_type="entity",
            entity_id=entity_id,
            operation_count=len(operations),
        )
        if not _execute_entity_database_operations_with_agent(
            state,
            updated,
            detail,
        ):
            execute_entity_database_operations(
                detail,
                workspace_root=_detail_workspace_options(state).get("workspace_root"),
            )
    return updated


def _execute_entity_database_operations_with_agent(
    state: ProjectState,
    project_plan: dict[str, Any],
    detail: dict[str, Any],
    database_change_plan: dict[str, Any] | None = None,
    require_approval: bool = False,
) -> bool:
    """按 Database Agent 流程执行实体设计确认后的建表/补列 DDL。

    与 build 阶段数据库任务一致：先扫描真实库表，与目标表结构 diff，
    由 Deep Agent 生成 DDL 计划，风险分级后执行并复查；仅在确实存在
    create_table / add_column 操作且有工作区时才接管，其余情况返回
    False 走确定性执行。
    """

    workspace_root = _detail_workspace_options(state).get("workspace_root")
    if not workspace_root:
        return False
    database_design = (
        detail.get("database_design")
        if isinstance(detail.get("database_design"), dict)
        else {}
    )
    operations = [
        item
        for item in database_design.get("database_operations", [])
        if isinstance(item, dict)
    ]
    create_tables = [
        operation
        for operation in operations
        if str(operation.get("operation") or "") == "create_table"
    ]
    add_columns = [
        operation
        for operation in operations
        if str(operation.get("operation") or "") == "add_column"
    ]
    if not create_tables and not add_columns:
        return False
    gaps: list[dict[str, Any]] = []
    tables: list[str] = []
    for operation in create_tables:
        table = (
            operation.get("table")
            if isinstance(operation.get("table"), dict)
            else {}
        )
        table_name = str(table.get("name") or "")
        if not table_name:
            continue
        tables.append(table_name)
        gaps.append(
            {
                "kind": "missing_table",
                "table": table_name,
                "required": table,
            }
        )
    for operation in add_columns:
        table_name = str(operation.get("table") or "")
        column_name = str(operation.get("column") or "")
        to = operation.get("to") if isinstance(operation.get("to"), dict) else {}
        if not table_name or not column_name:
            continue
        if table_name not in tables:
            tables.append(table_name)
        gaps.append(
            {
                "kind": "missing_column",
                "table": table_name,
                "column": column_name,
                "required": {
                    "name": column_name,
                    "type": str(to.get("type") or "VARCHAR(255)"),
                    "nullable": to.get("nullable") is not False,
                    "comment": str(to.get("comment") or ""),
                },
            }
        )
    if not gaps:
        return False
    entity_id = str(detail.get("entity_id") or "entity")
    task = {
        "id": f"entity-design-ddl-{entity_id}",
        "owner": "database",
        # 建表属于结构性 DDL，强制进入审批流程；补列按 Agent 风险分级。
        "risk": "high" if (create_tables and require_approval) else "low",
        "summary": f"为实体 {entity_id} 落地数据库 DDL（建表/补列）",
        "database_scope": {
            "tables": tables,
            "gaps": gaps,
        },
    }
    try:
        results = generate_database_with_deep_agent(
            project_plan=project_plan,
            build_task_plan={},
            tasks=[task],
            workspace=workspace_root,
            selected_skill_names=None,
            database_change_plan=database_change_plan,
        )
    except Exception as exc:
        results = [
            {
                "task_id": task["id"],
                "status": "failed",
                "failure_category": "runner_crash",
                "failure_reason": f"{type(exc).__name__}: {exc}",
                "agent_note": f"数据库 DDL Agent 执行失败：{type(exc).__name__}: {exc}",
            }
        ]
    task_result = results[0] if results else {}
    execution = (
        task_result.get("database_execution")
        if isinstance(task_result.get("database_execution"), dict)
        else {}
    )
    detail["database_execution"] = {
        **execution,
        "operation_ids": [
            str(operation.get("id") or "") for operation in operations
        ],
        "approved_by": "entity_design_confirmation",
        "agent_note": str(task_result.get("agent_note") or ""),
        "plan": task_result.get("plan"),
        "risk": task_result.get("risk"),
        "task_status": str(task_result.get("status") or ""),
        "approval_required": (
            str(task_result.get("failure_category") or "")
            == "database_approval_required"
        ),
        "database_approval": task_result.get("database_approval"),
        "database_change_plan": task_result.get("database_change_plan"),
        "database_risk": task_result.get("database_risk"),
    }
    detail["table_operations_executed"] = (
        execution.get("status") == "completed"
    )
    return True


def _execute_entity_add_columns_with_agent(
    state: ProjectState,
    project_plan: dict[str, Any],
    entity_id: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    """点击“补充字段”后立即进入 DDL 生成阶段并执行补列，成功后由前端写入映射。"""

    table_name = str(action.get("table_name") or "").strip()
    fields = [
        item
        for item in action.get("fields", [])
        if isinstance(item, dict) and str(item.get("entity_field") or "").strip()
    ]
    columns = [str(item.get("entity_field") or "") for item in fields]
    if not table_name or not fields:
        return {
            "status": "failed",
            "table_name": table_name,
            "columns": columns,
            "message": "缺少补列参数。",
        }
    workspace_root = _detail_workspace_options(state).get("workspace_root")
    if not workspace_root:
        return {
            "status": "failed",
            "table_name": table_name,
            "columns": columns,
            "message": "缺少工作区路径，无法执行 DDL。",
        }
    operations = [
        {
            "id": f"add_{table_name}_{str(item.get('entity_field') or '')}",
            "operation": "add_column",
            "table": table_name,
            "column": str(item.get("entity_field") or ""),
            "to": {
                "type": str(item.get("type") or "VARCHAR(255)"),
                "nullable": item.get("nullable") is not False,
                "comment": str(item.get("comment") or ""),
            },
            "approved_by_user": True,
        }
        for item in fields
    ]
    detail = {
        "entity_id": entity_id,
        "database_design": {"database_operations": operations},
    }
    _execute_entity_database_operations_with_agent(
        state,
        project_plan,
        detail,
        action.get("database_change_plan")
        if isinstance(action.get("database_change_plan"), dict)
        else None,
        True,
    )
    execution = (
        detail.get("database_execution")
        if isinstance(detail.get("database_execution"), dict)
        else {}
    )
    if execution.get("approval_required"):
        return {
            "status": "approval_required",
            "table_name": table_name,
            "columns": columns,
            "message": "高危数据库操作需要审批后才能执行。",
            "approval": execution.get("database_approval"),
            "database_change_plan": execution.get("database_change_plan"),
            "risk": execution.get("database_risk"),
            "execution": execution,
        }
    if execution.get("status") == "skipped":
        return {
            "status": "already_satisfied",
            "table_name": table_name,
            "columns": columns,
            "message": "字段已存在，无需补充。",
            "execution": execution,
        }
    status = "completed" if execution.get("status") == "completed" else "failed"
    return {
        "status": status,
        "table_name": table_name,
        "columns": columns,
        "message": (
            f"补列 DDL 执行完成，已补充字段：{'、'.join(columns) or '无'}。"
            if status == "completed"
            else str(
                execution.get("failure_reason")
                or execution.get("summary")
                or "补列 DDL 执行失败。"
            )
        ),
        "execution": execution,
    }


def _execute_entity_create_table_action(
    state: ProjectState,
    project_plan: dict[str, Any],
    entity_id: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    """点击“确认建表并应用”后立即按 Database Agent 流程生成并执行建表 DDL。"""

    proposal = (
        action.get("proposal")
        if isinstance(action.get("proposal"), dict)
        else {}
    )
    table_name = str(proposal.get("name") or "").strip()
    if not table_name:
        return {
            "status": "failed",
            "table_name": "",
            "columns": [],
            "message": "缺少建表方案。",
        }
    workspace_root = _detail_workspace_options(state).get("workspace_root")
    if not workspace_root:
        return {
            "status": "failed",
            "table_name": table_name,
            "columns": [],
            "message": "缺少工作区路径，无法执行 DDL。",
        }
    detail = {
        "entity_id": entity_id,
        "database_design": {
            "database_operations": [
                {
                    "id": f"create_{table_name}",
                    "operation": "create_table",
                    "table": proposal,
                    "to": {},
                    "approved_by_user": True,
                }
            ]
        },
    }
    _execute_entity_database_operations_with_agent(
        state,
        project_plan,
        detail,
        action.get("database_change_plan")
        if isinstance(action.get("database_change_plan"), dict)
        else None,
    )
    execution = (
        detail.get("database_execution")
        if isinstance(detail.get("database_execution"), dict)
        else {}
    )
    if execution.get("approval_required"):
        return {
            "status": "approval_required",
            "table_name": table_name,
            "columns": [],
            "message": "高危数据库操作需要审批后才能执行。",
            "approval": execution.get("database_approval"),
            "database_change_plan": execution.get("database_change_plan"),
            "risk": execution.get("database_risk"),
            "execution": execution,
        }
    if execution.get("status") == "skipped":
        return {
            "status": "already_satisfied",
            "table_name": table_name,
            "columns": [],
            "message": "数据表已存在，无需创建。",
            "execution": execution,
        }
    status = "completed" if execution.get("status") == "completed" else "failed"
    return {
        "status": status,
        "table_name": table_name,
        "columns": [],
        "message": (
            f"建表 DDL 执行完成，已创建数据表 {table_name}。"
            if status == "completed"
            else str(
                execution.get("failure_reason")
                or execution.get("summary")
                or "建表 DDL 执行失败。"
            )
        ),
        "execution": execution,
    }


def _entity_ddl_approval_payload(ddl_execution: dict[str, Any]) -> dict[str, Any]:
    """把实体设计补列 DDL 的高危审批结果转换为 Workflow clarification。"""

    approval = (
        ddl_execution.get("approval")
        if isinstance(ddl_execution.get("approval"), dict)
        else {}
    )
    risk = (
        ddl_execution.get("risk")
        if isinstance(ddl_execution.get("risk"), dict)
        else {}
    )
    plan = (
        ddl_execution.get("database_change_plan")
        if isinstance(ddl_execution.get("database_change_plan"), dict)
        else {}
    )
    statements = (
        plan.get("statements") if isinstance(plan.get("statements"), list) else []
    )
    return {
        "mode": "agent_approval",
        "status": "requires_user_input",
        "message": "实体设计补列 DDL 属于高危数据库操作，需要审批后才能执行。",
        "approval": approval,
        "tool": approval.get("tool") or "database.execute",
        "risk": risk,
        "database_change_plan": plan,
        "questions": [
            {
                "id": "database_approval",
                "header": "数据库审批",
                "question": str(
                    approval.get("description")
                    or "是否批准执行该高危数据库变更计划？"
                ),
                "type": "text",
                "placeholder": "在审批卡片中批准或拒绝；批准后继续当前工作流。",
            }
        ],
        "context": {
            "taskId": "",
            "subject": approval.get("subject"),
            "details": approval.get("details"),
            "statementCount": len(statements),
        },
    }


def _entity_design_required_payload(error: EntityDesignRequiredError) -> dict:
    """接口/页面详细设计前置门禁：要求先完成绑定实体的实体设计并确认。"""

    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="实体设计门禁",
                question=(
                    "接口/页面详细设计开始前，必须先完成其绑定实体的实体设计并确认。"
                    f"{error.reason}"
                    "请先回到左侧大纲选择对应实体完成实体设计。"
                ),
                type="text",
                placeholder="例如：先完成订单实体的数据源选择与绑定确认。",
            )
        ]
    )
    payload["mode"] = "entity_design_required"
    payload["message"] = "存在未完成实体设计的实体，接口/页面详细设计已暂停。"
    payload["reason"] = error.reason
    payload["missing_entities"] = [
        {
            "entity_id": str(item.get("entity_id") or ""),
            "entity_name": str(
                item.get("entity_name") or item.get("entity_id") or ""
            ),
        }
        for item in error.missing_entities
        if isinstance(item, dict) and str(item.get("entity_id") or "").strip()
    ]
    return payload


def _normalize_detail_page(page: dict) -> dict:
    """把正式计划中的 id 兼容映射为细节设计内部使用的 pageId。"""

    pageId = str(page.get("pageId") or page.get("id") or "").strip()
    if not pageId or page.get("pageId") == pageId:
        return page
    return {**page, "pageId": pageId}


def _drop_legacy_detail_fields(project_plan: dict) -> None:
    """清理旧版数据源详细设计字段，避免新 endpoint 流程继续透传。"""

    project_plan.pop("data_source_detail_plans", None)
    project_plan.pop("data_source_detail_confirmation_summary", None)


def _selected_detail_plans(
    project_plan: dict,
    selectedPageId: str,
    *,
    selected_api_contract_id: str | None = None,
    selected_endpoint_id: str | None = None,
    selected_entity_id: str | None = None,
) -> list[dict]:
    """只返回当前页面、当前 endpoint 或当前实体的详细设计。"""

    if selected_entity_id:
        return [
            detail
            for detail in project_plan.get("entity_detail_plans", [])
            if isinstance(detail, dict)
            and str(detail.get("entity_id") or "") == selected_entity_id
        ]
    if selected_endpoint_id:
        return [
            detail
            for detail in project_plan.get("endpoint_detail_plans", [])
            if isinstance(detail, dict)
            and str(detail.get("api_contract_id") or "") == str(selected_api_contract_id or "")
            and str(detail.get("endpoint_id") or "") == selected_endpoint_id
        ]

    selected_page_details = [
        detail
        for detail in project_plan.get("page_detail_plans", [])
        if isinstance(detail, dict)
        and str(detail.get("pageId") or "") == selectedPageId
    ]
    return selected_page_details


def _has_selected_page_detail(project_plan: dict, selectedPageId: str) -> bool:
    """判断计划中是否已经包含当前页面的详情正文。"""

    return any(
        isinstance(detail, dict)
        and str(detail.get("pageId") or "") == selectedPageId
        for detail in project_plan.get("page_detail_plans", [])
    )


def _page_endpoint_details_complete(project_plan: dict, selectedPageId: str) -> bool:
    """判断页面声明的全部 endpoint 是否已有可独立复用的正式详情。"""

    references = extract_page_detail_context(project_plan, selectedPageId).get("references", {})
    for dependency in references.get("endpoint_dependencies", []):
        if not isinstance(dependency, dict):
            return False
        api_contract_id, endpoint_id = _resolve_endpoint_dependency(project_plan, dependency)
        if _find_formal_endpoint_detail(project_plan, api_contract_id, endpoint_id) is None:
            return False
    return True


def _has_selected_endpoint_detail(
    project_plan: dict,
    selected_api_contract_id: str,
    selected_endpoint_id: str,
) -> bool:
    """判断计划中是否已经包含当前 endpoint 的详情正文。"""

    return any(
        isinstance(detail, dict)
        and str(detail.get("api_contract_id") or "") == selected_api_contract_id
        and str(detail.get("endpoint_id") or "") == selected_endpoint_id
        and _has_formal_endpoint_detail_content(detail)
        for detail in project_plan.get("endpoint_detail_plans", [])
    )


def _has_selected_entity_detail(
    project_plan: dict,
    selected_entity_id: str,
) -> bool:
    """判断计划中是否已经包含当前实体的详情正文。"""

    return any(
        isinstance(detail, dict)
        and str(detail.get("entity_id") or "") == selected_entity_id
        for detail in project_plan.get("entity_detail_plans", [])
    )


def _has_formal_endpoint_detail_content(detail: dict) -> bool:
    """判断 endpoint 详情是否包含可供用户确认的正式设计内容，接口不依赖数据源。"""

    return all(
        isinstance(detail.get(field), dict) and bool(detail.get(field))
        for field in ("data_usage", "interface_design")
    )


def _selected_detail_design_targets(
    project_plan: dict,
    selectedPageId: str,
    *,
    selected_api_contract_id: str | None = None,
    selected_endpoint_id: str | None = None,
    selected_entity_id: str | None = None,
) -> list[dict]:
    """把全量目标清单收敛到当前页面、当前 endpoint 或当前实体。"""

    selected_plans = _selected_detail_plans(
        project_plan,
        selectedPageId,
        selected_api_contract_id=selected_api_contract_id,
        selected_endpoint_id=selected_endpoint_id,
        selected_entity_id=selected_entity_id,
    )
    if selected_entity_id:
        return [
            {
                "id": selected_entity_id,
                "type": "entity",
                "label": f"实体：{plan.get('entity_name') or selected_entity_id}",
                "name": plan.get("entity_name") or selected_entity_id,
                "description": plan.get("description") or "",
            }
            for plan in selected_plans
        ]
    if selected_endpoint_id:
        return [
            {
                "id": f"{selected_api_contract_id}:{selected_endpoint_id}",
                "type": "endpoint",
                "label": f"接口：{plan.get('method')} {plan.get('path')}",
                "name": plan.get("name") or selected_endpoint_id,
                "description": plan.get("summary") or "",
            }
            for plan in selected_plans
        ]
    selected_ids = {
        str(plan.get("pageId") or "")
        for plan in selected_plans
        if isinstance(plan, dict) and plan.get("pageId")
    }
    return [
        target
        for target in detail_design_targets(project_plan)
        if str(target.get("id") or "") in selected_ids
    ]


def _project_plan_json_path_for_state(state: ProjectState) -> str:
    if state.get("workflow_scope") == "application_planning":
        return str(state.get("technical_plan_json_path") or technical_plan_json_path(state))
    return str(state.get("project_plan_json_path") or project_plan_json_path(state))


def _project_plan_confirmation_payload(project_plan: dict) -> dict:
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="计划确认",
                question=(
                    "请确认已生成的项目规划书是否正确。"
                    "如果正确，请回复“正确，继续”；"
                    "如果需要调整，请直接写出要修改的架构、API、页面、数据源、权限或验收标准。"
                ),
                type="text",
                placeholder="例如：正确，继续 / 需要增加库存盘点页面和盘点记录数据源。",
            )
        ]
    )
    payload["mode"] = "project_plan_confirmation"
    payload["message"] = "请确认项目计划后再继续页面/接口细节设计。"
    payload["plan_summary"] = project_plan.get("app", {}).get("name", "未命名应用")
    return payload


def _project_plan_dependency_error_payload(errors: list[str]) -> dict:
    """要求用户修订 ProjectPlan 中无法自动修复的依赖或 API 契约缺口。"""

    error_summary = _project_plan_dependency_error_summary(errors)
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="计划一致性校验",
                question=(
                    "系统已自动尝试修复项目计划中的页面依赖和 API 契约，但仍有无法安全推断的问题。"
                    f"{error_summary}"
                    "请补充业务决策后，我会重新生成项目计划；无需手动编辑 JSON。"
                ),
                type="text",
                placeholder="例如：为入职表单补充 create endpoint，并修正页面路由。",
            )
        ]
    )
    payload["mode"] = "project_plan_dependency_validation_error"
    payload["message"] = "项目计划自动修复后仍未通过一致性校验，页面设计未开始。"
    payload["errors"] = errors
    return payload


def _technical_plan_generation_error_payload(errors: list[str]) -> dict:
    """在有界自动修复耗尽后返回可重试状态，避免把非法 JSON 当成正式产物。"""

    return {
        "mode": "technical_plan_generation_error",
        "status": "requires_user_input",
        "message": "技术规划自动修复后仍未通过校验。",
        "errors": [str(error).strip() for error in errors if str(error).strip()][:8],
        "questions": [],
    }


def _project_plan_dependency_error_summary(errors: list[str]) -> str:
    """把计划一致性错误压缩成用户可见的简短问题清单。"""

    visible_errors = [
        str(error).strip()
        for error in errors
        if str(error).strip()
    ][:5]
    if not visible_errors:
        return ""
    return "当前剩余问题：" + "；".join(visible_errors) + "。"


def _project_plan_validation_errors(
    project_plan: dict,
    state: ProjectState | None = None,
) -> list[str]:
    """汇总 ProjectPlan 通用错误及创建流程的 TechnicalPlan 页面契约错误。"""

    validation_plan = project_plan
    if (
        state is not None
        and state.get("workflow_scope") == "application_planning"
        and project_plan.get("artifact_type") == TECHNICAL_PLAN_ARTIFACT_TYPE
        and isinstance(state.get("requirement_spec"), dict)
        and isinstance(state.get("product_plan"), dict)
        and isinstance(state.get("ui_designs"), dict)
    ):
        validation_plan = materialize_technical_plan_runtime(
            project_plan,
            state["requirement_spec"],
            state["product_plan"],
            state["ui_designs"],
        )
    errors = [
        *validate_project_plan_dependencies(validation_plan),
        *validate_api_contract_consistency(validation_plan),
        *validate_project_plan_datasource_policy(validation_plan),
    ]
    # 实体定义严格规则继续暂停；API entity_ids、字段引用和分页规则在确认前启用。
    if (
        state is not None
        and state.get("workflow_scope") == "application_planning"
        and project_plan.get("artifact_type") == TECHNICAL_PLAN_ARTIFACT_TYPE
        and isinstance(state.get("requirement_spec"), dict)
    ):
        errors.extend(
            validate_technical_plan_api_contracts(
                project_plan,
                state["requirement_spec"],
            )
        )
        # 临时暂停页面行为闭合校验；页面 Endpoint 结构和 API 契约校验继续执行。
        # errors.extend(_technical_plan_contract_errors(state, project_plan))
    return errors


def _technical_plan_retry_feedback(errors: list[str]) -> str:
    """把页面契约等确定性错误压缩成 TechnicalPlan 自动修复指令。"""

    diagnostics = "\n".join(f"- {error}" for error in errors[:12])
    return (
        "系统 TechnicalPlan 一致性校验未通过。请基于已确认的 ProductPlan 与 UiManifest，"
        "在本次重新生成中返回完整 TechnicalPlan 并修复下列问题；不得猜测或省略业务 action/step，"
        "必须完整保留 RequirementSpec 实体并通过 entity_ids 绑定 API Contract，禁止 data_source_id；"
        "每个 endpointId 必须同时存在于 api_contracts 和对应页面 endpoint_dependencies。"
        "不要要求用户重试，也不要解释校验过程：\n"
        f"{diagnostics}"
    )


def _generate_valid_technical_plan(
    state: ProjectState,
    requirement_spec: dict,
    existing_plan: dict | None,
) -> tuple[dict | None, list[str]]:
    """在统一的三次总预算内完成 TechnicalPlan 生成、规范化校验与错误反馈修复。"""

    current_plan = existing_plan
    remaining_errors: list[str] = []
    base_feedback = str(requirement_spec.get("planning_adjustment_request") or "").strip()
    for attempt in range(1, _TECHNICAL_PLAN_GENERATION_ATTEMPTS + 1):
        retry_feedback = (
            _technical_plan_retry_feedback(remaining_errors)
            if remaining_errors
            else base_feedback
        )
        current_requirement = {
            **requirement_spec,
            **(
                {"planning_adjustment_request": retry_feedback}
                if retry_feedback
                else {}
            ),
        }
        try:
            candidate = plan_project_with_chat_model(
                current_requirement,
                **({"existing_plan": current_plan} if current_plan else {}),
                on_token=_planning_token_callback,
            )
            candidate = apply_project_plan_feedback(candidate, retry_feedback)
            candidate = _attach_technical_plan_contracts(state, candidate)
            candidate["confirmation_status"] = "pending_user_confirmation"
            remaining_errors = _project_plan_validation_errors(candidate, state)
            if not remaining_errors:
                return candidate, []
            current_plan = candidate
        except (TypeError, ValueError) as exc:
            remaining_errors = [str(exc)]
        logger.warning(
            "technical_plan_generation_retry: attempt=%s/%s errors=%s",
            attempt,
            _TECHNICAL_PLAN_GENERATION_ATTEMPTS,
            remaining_errors,
        )
    return None, remaining_errors


def _repair_technical_plan_validation_errors(
    state: ProjectState,
    project_plan: dict,
    errors: list[str],
    *,
    repair_attempts: int,
) -> tuple[dict, list[str]]:
    """保留已确认上游上下文，对 TechnicalPlan 执行有界生成、编译和复验循环。"""

    requirement_spec = state.get("requirement_spec")
    if not isinstance(requirement_spec, dict):
        return project_plan, ["TechnicalPlan 必须读取已确认的 RequirementSpec。"]
    technical_requirement = _technical_planning_requirement_spec(
        state,
        requirement_spec,
    )
    current_plan = project_plan
    remaining_errors = errors
    for attempt in range(1, max(repair_attempts, 0) + 1):
        feedback = _technical_plan_retry_feedback(remaining_errors)
        logger.warning(
            "technical_plan_validation_retry: attempt=%s/%s errors=%s",
            attempt,
            repair_attempts,
            remaining_errors,
        )
        try:
            repaired = plan_project_with_chat_model(
                {
                    **technical_requirement,
                    "planning_adjustment_request": feedback,
                },
                existing_plan=current_plan,
                on_token=_planning_token_callback,
            )
            repaired = apply_project_plan_feedback(
                repaired,
                feedback,
            )
            current_plan = _attach_technical_plan_contracts(state, repaired)
            current_plan["confirmation_status"] = "pending_user_confirmation"
            remaining_errors = _project_plan_validation_errors(
                current_plan,
                state,
            )
        except ValueError as exc:
            remaining_errors = [str(exc)]
        if not remaining_errors:
            return current_plan, []
    return current_plan, remaining_errors


def _repair_project_plan_validation_errors(
    project_plan: dict,
    errors: list[str],
    *,
    state: ProjectState | None = None,
    repair_attempts: int = 1,
) -> tuple[dict, list[str]]:
    """按流程类型把确定性错误回灌给规划模型，并限制自动修订次数。"""

    if state is not None and state.get("workflow_scope") == "application_planning":
        return _repair_technical_plan_validation_errors(
            state,
            project_plan,
            errors,
            repair_attempts=repair_attempts,
        )

    feedback = "系统计划一致性校验失败，请在本次重新生成中完整修复以下问题：\n" + "\n".join(
        f"- {error}" for error in errors
    )
    repaired = revise_project_plan_with_chat_model(
        project_plan,
        feedback,
        on_token=_planning_token_callback,
    )
    repaired["confirmation_status"] = "pending_user_confirmation"
    return repaired, _project_plan_validation_errors(repaired, state)


def _project_plan_revision_required_payload(reason: str) -> dict:
    """页面设计发现依赖缺口时阻止自由扩展，并要求修订 ProjectPlan。"""

    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="需要修订计划",
                question="页面设计需要尚未声明的 endpoint 或跳转目标，不能自由添加。请返回项目计划修订依赖后重新确认。",
                type="text",
                placeholder="例如：在入职页面的 endpoint_dependencies 中补充员工创建接口。",
            )
        ]
    )
    payload["mode"] = "project_plan_revision_required"
    payload["message"] = "页面设计已停止，必须先修订并重新确认项目计划。"
    payload["reason"] = reason
    return payload


def _project_plan_confirmed_payload(project_plan: dict) -> dict:
    return {
        "mode": "project_plan_confirmation",
        "status": "clear",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": [],
        "assumptions": [],
        "message": "项目计划已由用户确认，可以继续后续流程。",
        "plan_summary": project_plan.get("app", {}).get("name", "未命名应用"),
    }


def _entity_design_confirmed_payload(
    project_plan: dict,
    *,
    selected_entity_id: str = "",
    detail_target_type: str = "entity",
) -> dict:
    """实体设计确认后的完成载荷：保留已确认实体设计的 review 摘要，
    前端据此继续展示确认卡片（锁定态），避免确认后上下文丢失。"""

    payload = detail_review_payload(
        project_plan,
        selected_entity_id=selected_entity_id or None,
        detail_target_type=detail_target_type or "entity",
    )
    payload["status"] = "clear"
    payload["message"] = (
        f"实体 `{selected_entity_id}` 详细设计已确认并保存，可以继续后续流程。"
    )
    return payload


def _user_confirmed_project_plan(request: str) -> bool:
    return user_confirmed_text(
        request,
        positive_signals=("正确", "没问题", "继续", "可以继续", "无误", "确认"),
        negative_signals=(
            "不正确",
            "需要修改",
            "要修改",
            "请修改",
            "想修改",
            "修改一下",
            "去修改",
            "重新修改",
            "需要调整",
            "要调整",
            "请调整",
            "调整一下",
            "需要补充",
            "要补充",
            "请补充",
            "补充一下",
            "不对",
        ),
    )


def _has_explicit_user_submission(state: ProjectState) -> bool:
    """创建规划只接受原生中断恢复写入的计划交互。"""

    return (
        state.get("workflow_scope") != "application_planning"
        or bool(state.get("application_planning_interaction"))
    )
