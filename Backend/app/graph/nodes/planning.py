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
    repair_technical_plan_api_contracts_with_chat_model,
    revise_project_plan_with_chat_model,
    technical_plan_contract_repair_applicable,
)
from app.graph.nodes.confirmation import user_confirmed_text
from app.graph.nodes.common import workspace_from_state
from app.graph.state import ProjectState
from app.services.api_contract_validation import (
    validate_api_contract_consistency,
    validate_api_contract_definitions,
)
from app.services.entity_source_binding import (
    apply_entity_source_binding_submission,
    entity_source_binding_payload,
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
from app.services.authorization_deliverability import (
    authorization_deliverability_errors,
    authorization_deliverability_report,
)
from app.services.product_plan import require_current_product_plan
from app.services.page_dependencies import (
    close_page_action_endpoint_dependencies,
    validate_project_plan_dependencies,
)
from app.services.page_implementation_contract import (
    attach_page_implementation_contracts,
    materialize_technical_plan_runtime,
    validate_page_implementation_contracts,
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
    if (
        not isinstance(product_plan, dict)
        or product_plan.get("confirmation_status") != "confirmed"
    ):
        raise ValueError("TechnicalPlan 必须基于已确认 ProductPlan 生成。")
    if not isinstance(ui_designs, dict) or ui_designs.get(
        "confirmation_status"
    ) not in {
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
    closed_plan = {
        **plan,
        "pages": close_page_action_endpoint_dependencies(
            [page for page in plan.get("pages", []) if isinstance(page, dict)],
            [
                contract
                for contract in plan.get("api_contracts", [])
                if isinstance(contract, dict)
            ],
        ),
    }
    return attach_page_implementation_contracts(closed_plan, product_plan, ui_designs)


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
    requirement_spec = state.get("requirement_spec")
    errors = validate_page_implementation_contracts(plan, product_plan, ui_designs)
    if isinstance(requirement_spec, dict):
        report = authorization_deliverability_report(
            plan.get("authorization_manifest"),
            requirement_spec,
            product_plan,
            (
                plan.get("api_contracts")
                if isinstance(plan.get("api_contracts"), list)
                else []
            ),
            plan.get("pages") if isinstance(plan.get("pages"), list) else [],
            (
                plan.get("page_implementation_contracts")
                if isinstance(plan.get("page_implementation_contracts"), list)
                else []
            ),
        )
        errors.extend(authorization_deliverability_errors(report))
    return errors


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

    logger.info("entity_source_binding progress: %s %s", message, detail)
    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        writer = None
    if writer:
        writer(
            {
                "type": "entity_source_binding.progress",
                "node_name": "entity_source_binding",
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
        product_plan = require_current_product_plan(
            state.get("product_plan"), requirement_spec
        )
        if product_plan.get("confirmation_status") != "confirmed":
            raise ValueError("TechnicalPlan 必须基于已确认 ProductPlan 生成。")
    existing_plan = (
        state.get("technical_plan")
        if state.get("workflow_scope") == "application_planning"
        else state.get("project_plan")
    )
    repair_seed = state.get("technical_plan_repair_candidate")
    repair_errors = state.get("technical_plan_repair_errors")
    clarification = state.get("clarification")
    resume_failed_candidate = (
        phase == "technical_planning"
        and action == "revise"
        and isinstance(clarification, dict)
        and clarification.get("mode") == "technical_plan_generation_error"
        and isinstance(repair_seed, dict)
        and bool(repair_seed)
    )
    if resume_failed_candidate:
        # 外部“重新生成”继续修复服务端 checkpoint 中的失败候选，不从零重复生成。
        existing_plan = repair_seed
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
            **(
                {"technical_plan": existing_plan}
                if phase == "technical_planning"
                else {}
            ),
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
            "technical_plan_repair_candidate": {},
            "technical_plan_repair_errors": [],
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
        project_plan, validation_errors, failed_candidate = (
            _generate_valid_technical_plan(
                state,
                requirement_spec,
                existing_plan if isinstance(existing_plan, dict) else None,
                initial_errors=(
                    [str(error) for error in repair_errors]
                    if resume_failed_candidate and isinstance(repair_errors, list)
                    else None
                ),
            )
        )
        if project_plan is None:
            return {
                "phase": phase,
                "status": "requires_user_input",
                "clarification": _technical_plan_generation_error_payload(
                    validation_errors
                ),
                "technical_plan_repair_candidate": failed_candidate or {},
                "technical_plan_repair_errors": validation_errors[:12],
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
        "technical_plan_repair_candidate": {},
        "technical_plan_repair_errors": [],
        "timeline": [phase],
    }


def entity_source_binding(state: ProjectState) -> dict:
    """运行独立实体数据源绑定；页面/API 不再进入任何详设节点。"""

    selected_entity_id = str(state.get("selected_entity_id") or "").strip()
    if not selected_entity_id:
        raise ValueError("EntitySourceBinding 必须提供 selectedEntityId。")
    result = _entity_source_binding_implementation(
        {**state, "detail_target_type": "entity"}
    )
    result["phase"] = "entity_source_binding"
    result["timeline"] = ["entity_source_binding"]
    result["entity_source_binding_submission"] = {}
    clarification = result.get("clarification")
    if isinstance(clarification, dict):
        clarification.setdefault("workflow_phase", "entity_source_binding")
    return result


def _entity_source_binding_implementation(state: ProjectState) -> dict:
    """处理单个实体的数据源选择、物理绑定、数据库操作与显式确认。"""

    selected_entity_id = str(state.get("selected_entity_id") or "").strip()
    if not selected_entity_id:
        raise ValueError("EntitySourceBinding 必须提供 selectedEntityId。")
    pending_plan = state.get("pending_project_plan")
    submission = state.get("entity_source_binding_submission")
    selected_state = {
        "selected_entity_id": selected_entity_id,
        "detail_target_type": "entity",
    }

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
        base_plan = (
            pending_plan
            if isinstance(pending_plan, dict)
            else (
                state.get("project_plan")
                if isinstance(state.get("project_plan"), dict)
                else None
            )
        )
        if not isinstance(base_plan, dict):
            raise ValueError("缺少 TechnicalPlan，无法提交实体数据源绑定。")
        review_plan = _ensure_entity_detail_for_submit(
            state,
            base_plan,
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
                "overall_note": "实体数据源绑定单卡片确认",
            }

    # 节点每轮会用空对象清理确认状态；只有显式 confirmed 提交才能进入确认分支，
    # 否则 AI、DDL 等后续实体设计动作会被旧的空状态提前截断。
    has_confirmed_submission = (
        isinstance(submission, dict)
        and bool(submission)
        and str(submission.get("review_status") or "") == "confirmed"
    )
    if isinstance(pending_plan, dict) and has_confirmed_submission:
        entity_errors = _pending_entity_design_validation_errors(
            pending_plan,
            selected_entity_id,
        )
        if entity_errors:
            review_plan = _attach_entity_design_validation_errors(
                pending_plan,
                selected_entity_id,
                entity_errors,
            )
            return _entity_design_requires_revision(
                state,
                review_plan,
                selected_entity_id=selected_entity_id,
                detail_target_type="entity",
            )
        confirmed_plan = apply_entity_source_binding_submission(
            pending_plan,
            submission,
            selected_entity_id=selected_entity_id,
        )
        confirmed_plan = _execute_confirmed_entity_database_operations(
            state,
            confirmed_plan,
            selected_entity_id,
        )
        project_plan_path = write_project_plan_document(state, confirmed_plan)
        return {
            "phase": "entity_source_binding",
            "status": "completed",
            "project_plan": confirmed_plan,
            "pending_project_plan": {},
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "clarification": _entity_design_confirmed_payload(
                confirmed_plan,
                selected_entity_id=selected_entity_id,
                detail_target_type="entity",
            ),
            "detail_selection": {
                "status": "completed",
                "mode": "entity_review",
                "targets": [],
            },
            **selected_state,
            "detail_plans": _selected_detail_plans(
                confirmed_plan,
                "",
                selected_entity_id=selected_entity_id,
            ),
            "entity_design_action": {},
            "acceptance_adjustment": {},
            "timeline": ["entity_source_binding"],
        }

    if isinstance(pending_plan, dict) and _user_confirmed_project_plan(
        state.get("request", "")
    ):
        return _entity_source_binding_implementation(
            {
                **state,
                "entity_source_binding_submission": {
                    "review_status": "confirmed",
                    "target_changes": [],
                    "overall_note": "文本确认",
                },
            }
        )

    project_plan = state.get("project_plan")
    if not isinstance(project_plan, dict):
        raise ValueError("缺少已确认 TechnicalPlan，无法进行实体数据源绑定。")
    review_plan = pending_plan if isinstance(pending_plan, dict) else project_plan
    project_plan_path = state.get("project_plan_path")
    ai_suggestions: dict[str, Any] | None = None
    ddl_execution: dict[str, Any] | None = None
    action = state.get("entity_design_action")
    if (
        isinstance(action, dict)
        and str(action.get("entity_id") or "") == selected_entity_id
    ):
        action_name = str(action.get("action") or "")
        if action_name == "ai_assist":
            ai_suggestions = _build_entity_design_ai_suggestions(
                project_plan,
                selected_entity_id,
                action,
                workspace_root=_detail_workspace_options(state).get("workspace_root"),
            )
        elif action_name == "execute_add_columns":
            ddl_execution = _execute_entity_add_columns_with_agent(
                state,
                project_plan,
                selected_entity_id,
                action,
            )
        elif action_name == "execute_create_table":
            ddl_execution = _execute_entity_create_table_action(
                state,
                project_plan,
                selected_entity_id,
                action,
            )
        else:
            review_plan = _apply_entity_design_action(
                state,
                project_plan,
                review_plan,
                selected_entity_id,
            )
            review_plan["confirmation_status"] = "pending_user_confirmation"
            project_plan_path = write_project_plan_document(state, review_plan)
        if ddl_execution and ddl_execution.get("status") == "approval_required":
            return {
                "phase": "entity_source_binding",
                "status": "requires_user_input",
                "entity_design_action": {
                    **action,
                    "database_change_plan": ddl_execution.get("database_change_plan"),
                },
                "clarification": _entity_ddl_approval_payload(ddl_execution),
                "pending_project_plan": review_plan,
                "project_plan": project_plan,
                "project_plan_path": project_plan_path,
                "project_plan_json_path": _project_plan_json_path_for_state(state),
                **selected_state,
                "timeline": ["entity_source_binding"],
            }

    clarification = entity_source_binding_payload(
        review_plan,
        selected_entity_id=selected_entity_id,
        detail_target_type="entity",
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
        "phase": "entity_source_binding",
        "status": "requires_user_input",
        "clarification": clarification,
        "pending_project_plan": review_plan,
        "project_plan": project_plan,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": _project_plan_json_path_for_state(state),
        "detail_selection": {
            "status": "requires_user_input",
            "mode": "entity_review",
            **selected_state,
            "targets": _selected_detail_design_targets(
                review_plan,
                "",
                selected_entity_id=selected_entity_id,
            ),
        },
        **selected_state,
        "detail_plans": _selected_detail_plans(
            review_plan,
            "",
            selected_entity_id=selected_entity_id,
        ),
        "entity_design_action": {},
        "timeline": ["entity_source_binding"],
    }


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
            else (
                ENTITY_DESIGN_STAGE_EXTERNAL_API_INPUT
                if source_type == "external_api"
                else "static_design"
            )
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
        if (
            not isinstance(detail, dict)
            or str(detail.get("entity_id") or "") != entity_id
        ):
            continue
        source_type = str(detail.get("data_source_type") or "")
        section_key = {
            "database": "database_design",
            "external_api": "external_api_design",
            "static": "static_design",
        }.get(source_type)
        if section_key:
            section = (
                detail.get(section_key)
                if isinstance(detail.get(section_key), dict)
                else {}
            )
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
        "phase": "entity_source_binding",
        "status": "requires_user_input",
        "clarification": entity_source_binding_payload(
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
        "entity_source_binding_submission": {},
        "entity_design_action": {},
        "timeline": ["entity_source_binding"],
    }


def _execute_confirmed_entity_database_operations(
    state: ProjectState,
    project_plan: dict[str, Any],
    entity_id: str,
) -> dict[str, Any]:
    """实体设计确认后执行数据库表操作，并把执行证据写回实体详情。"""

    updated = deepcopy(project_plan)
    for detail in updated.get("entity_detail_plans", []):
        if (
            not isinstance(detail, dict)
            or str(detail.get("entity_id") or "") != entity_id
        ):
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
            operation.get("table") if isinstance(operation.get("table"), dict) else {}
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
        "operation_ids": [str(operation.get("id") or "") for operation in operations],
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
    detail["table_operations_executed"] = execution.get("status") == "completed"
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
        (
            action.get("database_change_plan")
            if isinstance(action.get("database_change_plan"), dict)
            else None
        ),
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
        action.get("proposal") if isinstance(action.get("proposal"), dict) else {}
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
        (
            action.get("database_change_plan")
            if isinstance(action.get("database_change_plan"), dict)
            else None
        ),
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
        ddl_execution.get("risk") if isinstance(ddl_execution.get("risk"), dict) else {}
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
                    approval.get("description") or "是否批准执行该高危数据库变更计划？"
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


def _selected_detail_plans(
    project_plan: dict,
    selectedPageId: str = "",
    *,
    selected_entity_id: str | None = None,
) -> list[dict]:
    """只返回当前 EntitySourceBinding 的实体绑定产物。"""

    del selectedPageId
    if not selected_entity_id:
        return []
    return [
        detail
        for detail in project_plan.get("entity_detail_plans", [])
        if isinstance(detail, dict)
        and str(detail.get("entity_id") or "") == selected_entity_id
    ]


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


def _selected_detail_design_targets(
    project_plan: dict,
    selectedPageId: str = "",
    *,
    selected_entity_id: str | None = None,
) -> list[dict]:
    """把 EntitySourceBinding 目标收敛到当前实体。"""

    selected_plans = _selected_detail_plans(
        project_plan,
        selectedPageId,
        selected_entity_id=selected_entity_id,
    )
    return [
        {
            "id": str(plan.get("entity_id") or selected_entity_id or ""),
            "type": "entity",
            "label": f"实体：{plan.get('entity_name') or selected_entity_id}",
            "name": plan.get("entity_name") or selected_entity_id,
            "description": plan.get("description") or "",
        }
        for plan in selected_plans
    ]


def _project_plan_json_path_for_state(state: ProjectState) -> str:
    if state.get("workflow_scope") == "application_planning":
        return str(
            state.get("technical_plan_json_path") or technical_plan_json_path(state)
        )
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

    visible_errors = [str(error).strip() for error in errors if str(error).strip()][:5]
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
        # 4E 必须读取运行时物化的 PageImplementationContract；正式 TechnicalPlan 不持久化该派生字段。
        errors.extend(_technical_plan_contract_errors(state, validation_plan))
    return list(dict.fromkeys(str(error).strip() for error in errors if str(error).strip()))


def _technical_plan_contract_validation_errors(
    requirement_spec: dict,
    project_plan: dict,
) -> list[str]:
    """汇总仅能通过替换 API Contract 修复的确定性错误。"""

    errors = [
        *validate_api_contract_definitions(project_plan),
        *validate_technical_plan_api_contracts(project_plan, requirement_spec),
    ]
    return list(dict.fromkeys(str(error).strip() for error in errors if str(error).strip()))


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


def _repair_technical_plan_candidate(
    requirement_spec: dict,
    current_plan: dict,
    errors: list[str],
) -> dict:
    """优先定向修复失败 Contract，无法定位或解析时才回退完整计划修订。"""

    contract_errors = _technical_plan_contract_validation_errors(
        requirement_spec,
        current_plan,
    )
    if technical_plan_contract_repair_applicable(
        current_plan,
        errors,
        contract_errors,
    ):
        return repair_technical_plan_api_contracts_with_chat_model(
            requirement_spec,
            current_plan,
            errors,
            on_token=_planning_token_callback,
        )
    logger.warning("technical_plan_contract_repair_fallback: errors=%s", errors)
    return plan_project_with_chat_model(
        requirement_spec,
        existing_plan=current_plan,
        on_token=_planning_token_callback,
    )


def _generate_valid_technical_plan(
    state: ProjectState,
    requirement_spec: dict,
    existing_plan: dict | None,
    *,
    initial_errors: list[str] | None = None,
) -> tuple[dict | None, list[str], dict | None]:
    """在统一的三次总预算内完成 TechnicalPlan 生成、规范化校验与错误反馈修复。"""

    current_plan = existing_plan
    remaining_errors = list(initial_errors or [])
    base_feedback = str(
        requirement_spec.get("planning_adjustment_request") or ""
    ).strip()
    if current_plan and remaining_errors:
        current_plan = _attach_technical_plan_contracts(state, current_plan)
        current_plan["confirmation_status"] = "pending_user_confirmation"
        remaining_errors = _project_plan_validation_errors(current_plan, state)
        if not remaining_errors:
            return current_plan, [], None
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
            candidate = (
                _repair_technical_plan_candidate(
                    current_requirement,
                    current_plan,
                    remaining_errors,
                )
                if current_plan and remaining_errors
                else plan_project_with_chat_model(
                    current_requirement,
                    **({"existing_plan": current_plan} if current_plan else {}),
                    on_token=_planning_token_callback,
                )
            )
            candidate = apply_project_plan_feedback(candidate, retry_feedback)
            candidate = _attach_technical_plan_contracts(state, candidate)
            candidate["confirmation_status"] = "pending_user_confirmation"
            remaining_errors = _project_plan_validation_errors(candidate, state)
            if not remaining_errors:
                return candidate, [], None
            current_plan = candidate
        except (TypeError, ValueError) as exc:
            remaining_errors = [str(exc)]
        logger.warning(
            "technical_plan_generation_retry: attempt=%s/%s errors=%s",
            attempt,
            _TECHNICAL_PLAN_GENERATION_ATTEMPTS,
            remaining_errors,
        )
    return None, remaining_errors, current_plan


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
            repaired = _repair_technical_plan_candidate(
                {
                    **technical_requirement,
                    "planning_adjustment_request": feedback,
                },
                current_plan,
                remaining_errors,
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

    feedback = (
        "系统计划一致性校验失败，请在本次重新生成中完整修复以下问题：\n"
        + "\n".join(f"- {error}" for error in errors)
    )
    repaired = revise_project_plan_with_chat_model(
        project_plan,
        feedback,
        on_token=_planning_token_callback,
    )
    repaired["confirmation_status"] = "pending_user_confirmation"
    return repaired, _project_plan_validation_errors(repaired, state)


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

    payload = entity_source_binding_payload(
        project_plan,
        selected_entity_id=selected_entity_id,
        detail_target_type=detail_target_type or "entity",
    )
    payload["status"] = "clear"
    payload["message"] = (
        f"实体 `{selected_entity_id}` 的数据源绑定已确认并保存；请重新选择页面或 API 开始开发。"
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

    return state.get("workflow_scope") != "application_planning" or bool(
        state.get("application_planning_interaction")
    )
