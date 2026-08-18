import logging

from langgraph.config import get_stream_writer

from app.agents.main.document_sync import sync_project_plan_from_markdown
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
from app.services.database_context import prepare_endpoint_database_context
from app.services.data_source_policy import (
    apply_authoritative_datasource_type,
    read_application_datasource_type,
)
from app.services.project_plan import (
    apply_project_plan_datasource_policy,
    apply_project_plan_feedback,
    TECHNICAL_PLAN_ARTIFACT_TYPE,
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
    """把已确认 ProductPlan 与 UI manifest 注入开发技术规划输入。"""

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
        "confirmed_ui_design_manifest": _technical_ui_manifest(ui_designs),
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
                    "请由开发角色确认技术架构、API、Schema、工程设计和页面 endpoint 引用。"
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
    """生成或确认 ProjectPlan，并始终执行应用数据源权威策略。"""

    phase = _planning_phase(state)
    if state.get("workflow_scope") == "application_planning":
        requirement_spec = state.get("requirement_spec")
        if not isinstance(requirement_spec, dict):
            raise ValueError("TechnicalPlan 必须读取已确认的 RequirementSpec。")
        product_plan = require_current_product_plan(state.get("product_plan"), requirement_spec)
        if product_plan.get("confirmation_status") != "confirmed":
            raise ValueError("TechnicalPlan 必须基于已确认 ProductPlan 生成。")
    datasource_type = read_application_datasource_type(workspace_from_state(state))
    existing_plan = (
        state.get("technical_plan")
        if state.get("workflow_scope") == "application_planning"
        else state.get("project_plan")
    )
    if isinstance(existing_plan, dict):
        existing_plan = apply_project_plan_datasource_policy(
            existing_plan,
            datasource_type,
        )
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
                datasource_type,
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
    if existing_plan and _user_confirmed_project_plan(
        state.get("request", "")
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
                datasource_type,
            )
            if edited_markdown is not None
            else existing_plan
        )
        project_plan = {
            **apply_project_plan_feedback(
                synchronized_plan,
                state.get("request", ""),
                datasource_type,
            ),
            "confirmation_status": "confirmed",
        }
        project_plan = _attach_technical_plan_contracts(state, project_plan)
        validation_errors = _project_plan_validation_errors(
            project_plan,
            datasource_type,
            state,
        )
        if validation_errors:
            repaired_plan, remaining_errors = _repair_project_plan_validation_errors(
                project_plan,
                validation_errors,
                datasource_type,
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

    requirement_spec = apply_authoritative_datasource_type(
        state["requirement_spec"],
        datasource_type,
    )
    requirement_spec = _technical_planning_requirement_spec(state, requirement_spec)
    if existing_plan and state.get("request"):
        requirement_spec = {
            **requirement_spec,
            "planning_adjustment_request": state["request"],
        }
    project_plan = plan_project_with_chat_model(
        requirement_spec,
        **(
            {"existing_plan": existing_plan}
            if existing_plan
            else {}
        ),
        datasource_type=datasource_type,
        on_token=_planning_token_callback,
    )
    project_plan = apply_project_plan_feedback(
        project_plan,
        state.get("request", ""),
        datasource_type,
    )
    project_plan = _attach_technical_plan_contracts(state, project_plan)
    project_plan["confirmation_status"] = "pending_user_confirmation"
    validation_errors = _project_plan_validation_errors(
        project_plan,
        datasource_type,
        state,
    )
    if validation_errors:
        project_plan, validation_errors = _repair_project_plan_validation_errors(
            project_plan,
            validation_errors,
            datasource_type,
            state=state,
            repair_attempts=(
                _TECHNICAL_PLAN_GENERATION_ATTEMPTS - 1
                if phase == "technical_planning"
                else 1
            ),
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
    """页面仅确认缺失 EndpointDetail；显式接口目标继续走独立接口详设。"""

    pending_plan = state.get("pending_project_plan")
    submission = state.get("detail_review_submission")
    selectedPageId = str(state.get("selectedPageId") or "")
    selected_api_contract_id = str(state.get("selected_api_contract_id") or "")
    selected_endpoint_id = str(state.get("selected_endpoint_id") or "")
    detail_target_type = str(
        state.get("detail_target_type")
        or ("endpoint" if selected_endpoint_id else "page" if selectedPageId else "")
    )
    selected_endpoint_state = {
        **({"selected_api_contract_id": selected_api_contract_id} if selected_api_contract_id else {}),
        **({"selected_endpoint_id": selected_endpoint_id} if selected_endpoint_id else {}),
        **({"detail_target_type": detail_target_type} if detail_target_type else {}),
    }
    if selectedPageId and detail_target_type != "endpoint":
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
            detail_target_type=detail_target_type,
            regenerate_endpoint_details=acceptance_adjustment_type
            in {"endpoint_change", "data_source_change"},
        )
    if acceptance_adjustment_type == "project_plan_change" and not has_detail_submission:
        return _regenerate_acceptance_detail_plan(
            state,
            selectedPageId=selectedPageId,
            selected_api_contract_id=selected_api_contract_id,
            selected_endpoint_id=selected_endpoint_id,
            detail_target_type=detail_target_type,
            regenerate_endpoint_details=True,
        )
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
        confirmed_plan = apply_detail_review_submission(
            synchronized_plan,
            submission,
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
        )
        project_plan_path = write_project_plan_document(state, confirmed_plan)
        return {
            "phase": "detail_confirmation",
            "status": "completed",
            "project_plan": confirmed_plan,
            "pending_project_plan": {},
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "clarification": _project_plan_confirmed_payload(confirmed_plan),
            "detail_selection": {
                "status": "completed",
                "mode": "batch_review",
                "targets": [],
            },
            "selectedPageId": selectedPageId or None,
            **selected_endpoint_state,
            "detail_plans": [
                *confirmed_plan.get("page_detail_plans", []),
                *confirmed_plan.get("endpoint_detail_plans", []),
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

    if pending_plan and (selectedPageId or selected_endpoint_id):
        review_plan = pending_plan
        project_plan_path = state.get("project_plan_path")
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
            review_plan = _generate_all_detail_plans(
                source_plan,
                selected_api_contract_id=selected_api_contract_id,
                selected_endpoint_id=selected_endpoint_id,
                detail_target_type=detail_target_type or "endpoint",
                **_detail_workspace_options(state),
            )
            review_plan["confirmation_status"] = "pending_user_confirmation"
            project_plan_path = write_project_plan_document(state, review_plan)
        clarification = detail_review_payload(
            review_plan,
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            detail_target_type=detail_target_type or None,
        )
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
                "mode": "batch_review",
                "selectedPageId": selectedPageId or None,
                **selected_endpoint_state,
                "targets": _selected_detail_design_targets(
                    review_plan,
                    selectedPageId,
                    selected_api_contract_id=selected_api_contract_id or None,
                    selected_endpoint_id=selected_endpoint_id or None,
                ),
            },
            "selectedPageId": selectedPageId or None,
            **selected_endpoint_state,
            "detail_plans": _selected_detail_plans(
                review_plan,
                selectedPageId,
                selected_api_contract_id=selected_api_contract_id or None,
                selected_endpoint_id=selected_endpoint_id or None,
            ),
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
    if not selectedPageId and not selected_endpoint_id:
        raise ValueError(
            "开始详细设计时必须提供 selectedPageId 或 selectedEndpointId。"
        )
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
            detail_target_type=detail_target_type or None,
            **_detail_workspace_options(state),
        )
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
    )
    return {
        "phase": "detail_confirmation",
        "status": "requires_user_input",
        "clarification": detail_review_payload(
            pending_plan,
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            detail_target_type=detail_target_type or None,
        ),
        "pending_project_plan": pending_plan,
        "project_plan": project_plan,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": _project_plan_json_path_for_state(state),
        "detail_selection": {
            "status": "requires_user_input",
            "mode": "batch_review",
            "selectedPageId": selectedPageId or None,
            **selected_endpoint_state,
            "targets": targets,
        },
        "selectedPageId": selectedPageId or None,
        **selected_endpoint_state,
        "detail_plans": _selected_detail_plans(
            pending_plan,
            selectedPageId,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
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
            **_detail_workspace_options(state),
        )
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
    detail_target_type: str,
    regenerate_endpoint_details: bool,
) -> dict:
    """根据验收反馈生成新的页面或接口详情版本，并重新停在确认门禁。"""

    project_plan = state.get("project_plan")
    adjustment = state.get("acceptance_adjustment")
    feedback = (
        str(adjustment.get("feedback") or "").strip()
        if isinstance(adjustment, dict)
        else ""
    )
    if not isinstance(project_plan, dict):
        raise ValueError("验收调整缺少当前已确认的 ProjectPlan。")
    if not selectedPageId and not selected_endpoint_id:
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "clarification": _project_plan_revision_required_payload(
                "验收调整缺少当前页面或接口目标。"
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
    return {
        "phase": "detail_confirmation",
        "status": "requires_user_input",
        "clarification": detail_review_payload(
            pending_plan,
            selectedPageId=selectedPageId or None,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
            detail_target_type=detail_target_type or None,
        ),
        "pending_project_plan": pending_plan,
        "project_plan": project_plan,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": _project_plan_json_path_for_state(state),
        "detail_selection": {
            "status": "requires_user_input",
            "mode": "batch_review",
            "selectedPageId": selectedPageId or None,
            **selected_endpoint_state,
            "targets": targets,
        },
        "selectedPageId": selectedPageId or None,
        **selected_endpoint_state,
        "detail_plans": _selected_detail_plans(
            pending_plan,
            selectedPageId,
            selected_api_contract_id=selected_api_contract_id or None,
            selected_endpoint_id=selected_endpoint_id or None,
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
    detail_target_type: str | None = None,
    workspace_root: str | None = None,
    user_request: str = "",
    regenerate_endpoint_details: bool = False,
) -> dict:
    """为显式 endpoint 或页面依赖的 endpoint 生成接口详细设计。"""

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
) -> tuple[dict, dict]:
    """复用独立 endpoint 设计链路生成详情并挂回 ProjectPlan 内存态。"""

    _detail_progress(
        "开始生成接口详细设计。",
        target_type="endpoint",
        api_contract_id=api_contract_id,
        endpoint_id=endpoint_id,
    )
    endpoint_context = extract_endpoint_detail_context(
        project_plan,
        api_contract_id,
        endpoint_id,
    )
    _detail_progress(
        "正在确认接口数据来源。",
        target_type="endpoint",
        api_contract_id=api_contract_id,
        endpoint_id=endpoint_id,
        data_source_id=endpoint_context.get("data_source_id"),
    )
    database_context = prepare_endpoint_database_context(
        project_plan,
        endpoint_context,
        workspace_root,
    )
    endpoint_context = {**endpoint_context, "database_context": database_context}
    _detail_progress(
        database_context.get("message") or "数据库上下文准备完成。",
        target_type="endpoint",
        api_contract_id=api_contract_id,
        endpoint_id=endpoint_id,
        database_context_status=database_context.get("status"),
        reason=database_context.get("reason"),
        enabled=database_context.get("enabled"),
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
        (
            "接口决策仍需用户确认，已暂停处理逻辑与验收标准组装。"
            if detail.get("design_stage") == "needs_user_confirmation"
            else "接口决策已闭合，完整接口详情已确定性组装。"
        ),
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
) -> list[dict]:
    """只返回当前页面或当前 endpoint 的详细设计。"""

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


def _has_formal_endpoint_detail_content(detail: dict) -> bool:
    """判断 endpoint 详情是否包含可供用户确认的正式三段设计内容。"""

    return all(
        isinstance(detail.get(field), dict) and bool(detail.get(field))
        for field in ("data_usage", "data_origin", "interface_design")
    )


def _selected_detail_design_targets(
    project_plan: dict,
    selectedPageId: str,
    *,
    selected_api_contract_id: str | None = None,
    selected_endpoint_id: str | None = None,
) -> list[dict]:
    """把全量目标清单收敛到当前页面或当前 endpoint。"""

    selected_plans = _selected_detail_plans(
        project_plan,
        selectedPageId,
        selected_api_contract_id=selected_api_contract_id,
        selected_endpoint_id=selected_endpoint_id,
    )
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
    datasource_type: str | None = None,
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
    ]
    if datasource_type is not None:
        errors.extend(
            validate_project_plan_datasource_policy(
                validation_plan,
                datasource_type,  # type: ignore[arg-type]
            )
        )
    if state is not None:
        errors.extend(_technical_plan_contract_errors(state, project_plan))
    return errors


def _technical_plan_retry_feedback(errors: list[str]) -> str:
    """把页面契约等确定性错误压缩成 TechnicalPlan 自动修复指令。"""

    diagnostics = "\n".join(f"- {error}" for error in errors[:12])
    return (
        "系统 TechnicalPlan 一致性校验未通过。请基于已确认的 ProductPlan 与 UiManifest，"
        "在本次重新生成中返回完整 TechnicalPlan 并修复下列问题；不得猜测或省略业务 action/step，"
        "每个 endpointId 必须同时存在于 api_contracts 和对应页面 endpoint_dependencies。"
        "不要要求用户重试，也不要解释校验过程：\n"
        f"{diagnostics}"
    )


def _repair_technical_plan_validation_errors(
    state: ProjectState,
    project_plan: dict,
    errors: list[str],
    datasource_type: str | None,
    *,
    repair_attempts: int,
) -> tuple[dict, list[str]]:
    """保留已确认上游上下文，对 TechnicalPlan 执行有界生成、编译和复验循环。"""

    requirement_spec = state.get("requirement_spec")
    if not isinstance(requirement_spec, dict):
        return project_plan, ["TechnicalPlan 必须读取已确认的 RequirementSpec。"]
    technical_requirement = _technical_planning_requirement_spec(
        state,
        apply_authoritative_datasource_type(requirement_spec, datasource_type),  # type: ignore[arg-type]
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
                datasource_type=datasource_type,  # type: ignore[arg-type]
                on_token=_planning_token_callback,
            )
            repaired = apply_project_plan_feedback(
                repaired,
                feedback,
                datasource_type,  # type: ignore[arg-type]
            )
            current_plan = _attach_technical_plan_contracts(state, repaired)
            current_plan["confirmation_status"] = "pending_user_confirmation"
            remaining_errors = _project_plan_validation_errors(
                current_plan,
                datasource_type,
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
    datasource_type: str | None = None,
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
            datasource_type,
            repair_attempts=repair_attempts,
        )

    feedback = "系统计划一致性校验失败，请在本次重新生成中完整修复以下问题：\n" + "\n".join(
        f"- {error}" for error in errors
    )
    repaired = revise_project_plan_with_chat_model(
        project_plan, feedback,
        datasource_type=datasource_type,  # type: ignore[arg-type]
        on_token=_planning_token_callback,
    )
    repaired["confirmation_status"] = "pending_user_confirmation"
    return repaired, _project_plan_validation_errors(repaired, datasource_type, state)


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
    """创建规划只接受本轮确认卡提交，避免恢复文案越过计划门禁。"""

    return (
        state.get("workflow_scope") != "application_planning"
        or state.get("user_interaction_submission") is True
    )
