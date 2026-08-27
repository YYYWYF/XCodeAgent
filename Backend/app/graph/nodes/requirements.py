import json
import re
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer

from app.agents.main.document_sync import sync_requirement_spec_from_markdown
from app.agents.main.requirements_analyzer import (
    MAX_REQUIREMENT_CLARIFICATION_ROUNDS,
    analyze_requirements_with_chat_model,
)
from app.graph.nodes.confirmation import (
    extract_confirmation_answer,
    user_confirmed_text,
    user_requested_changes_text,
)
from app.graph.state import ProjectState
from app.services.data_source_policy import (
    apply_authoritative_datasource_type,
    datasource_type_from_artifact,
)
from app.services.application_authorization_config import (
    ApplicationAuthorizationConfigError,
    authorization_configuration_can_enable,
    persist_authorization_configuration,
)
from app.services.requirement_spec import (
    apply_requirement_spec_editor_changes,
    validate_authorization_requirements,
    validate_requirement_spec_confirmation_readiness,
)
from app.tools.ask_user import (
    clear_clarification,
)
from app.workspace.spec_documents import (
    confirmed_requirement_spec_json_path,
    edited_requirement_spec_markdown,
    requirement_spec_draft_json_path,
    requirement_spec_draft_markdown_path,
    requirement_spec_markdown_path,
    synchronize_requirement_spec_markdown_datasource_types,
    workspace_root,
    write_confirmed_requirement_spec_document,
    write_requirement_spec_draft_document,
)

logger = logging.getLogger("uvicorn.error")


def _clarification_round(state: dict) -> int:
    """读取并限制当前 RequirementSpec 已完成的澄清轮数。"""

    try:
        value = int(state.get("requirements_clarification_round", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return max(0, min(value, MAX_REQUIREMENT_CLARIFICATION_ROUNDS))


def _llm_token_callback(token: str) -> None:
    """将 LLM 流式 token 转发到 LangGraph custom stream。"""

    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        return
    writer({"type": "llm.token", "token": token, "node": "requirements"})


def prepare_requirement_spec_confirmation(
    state: ProjectState,
    existing_spec: dict[str, Any],
    *,
    datasource_type: str,
) -> tuple[dict[str, Any], str | None]:
    """同步并校验待确认 RequirementSpec，只返回内存候选，不提前写入正式文件。"""

    editor_changes = state.get("edited_requirement_spec")
    current_document_path = str(state.get("requirement_spec_path") or "").strip()
    has_current_document = (
        bool(current_document_path) and Path(current_document_path).is_file()
    )
    if isinstance(editor_changes, dict) and editor_changes:
        synchronized_spec = apply_requirement_spec_editor_changes(
            existing_spec,
            editor_changes,
            datasource_type=datasource_type,
        )
        edited_markdown = None
    else:
        # 用户可以直接编辑右侧草稿 Markdown；确认时只读取当前状态指向的文件，
        # 避免误读工作区里上一版正式文档。
        edited_markdown = (
            edited_requirement_spec_markdown(state, existing_spec)
            if has_current_document
            else None
        )
        synchronized_spec = (
            sync_requirement_spec_from_markdown(
                existing_spec,
                edited_markdown,
                datasource_type=datasource_type,
            )
            if edited_markdown is not None
            else existing_spec
        )
        synchronized_spec = apply_authoritative_datasource_type(
            synchronized_spec,
            datasource_type,
        )
    spec = {
        **synchronized_spec,
        "clarification_questions": [],
        "clarification_status": "clear",
        "confirmation_status": "confirmed",
    }
    confirmed_markdown: str | None = None
    markdown_path = requirement_spec_markdown_path(state)
    if (
        not (isinstance(editor_changes, dict) and editor_changes)
        and has_current_document
        and markdown_path.is_file()
    ):
        markdown_content = markdown_path.read_text(encoding="utf-8")
        synchronized_markdown = synchronize_requirement_spec_markdown_datasource_types(
            markdown_content,
            spec,
        )
        confirmed_markdown = synchronized_markdown
    readiness_errors = validate_requirement_spec_confirmation_readiness(spec)
    if readiness_errors:
        raise ValueError("确认前需求文档完整性校验失败：" + "；".join(readiness_errors))
    authorization_errors = validate_authorization_requirements(spec)
    if authorization_errors:
        raise ValueError("确认前权限需求校验失败：" + "；".join(authorization_errors))
    return spec, confirmed_markdown


def confirm_requirement_spec_artifact(
    state: ProjectState,
    existing_spec: dict[str, Any],
    *,
    datasource_type: str,
) -> dict:
    """兼容非联合流程的 RequirementSpec 正式写入入口。"""

    spec, confirmed_markdown = prepare_requirement_spec_confirmation(
        state,
        existing_spec,
        datasource_type=datasource_type,
    )
    spec_path = write_confirmed_requirement_spec_document(
        state,
        spec,
        markdown=confirmed_markdown,
    )
    return {
        "phase": "requirements",
        "status": "completed",
        "requirement_spec": spec,
        "requirements_confirmed": True,
        "requirements_clarification_round": 0,
        "requirement_spec_path": spec_path,
        "requirement_spec_json_path": str(confirmed_requirement_spec_json_path(state)),
        "edited_requirement_spec": {},
        "requirement_spec_feedback": "",
        "clarification": _requirement_spec_confirmed_payload(spec),
        "timeline": ["requirements"],
    }


def requirements(state: ProjectState) -> dict:
    """生成、修订或确认 RequirementSpec，并始终执行应用数据源策略保护。"""

    # 应用级不再有数据源类型；需求阶段只保留实体字段展示信息，数据源由实体设计决定。
    datasource_type = datasource_type_from_artifact(
        (
            state.get("requirement_spec")
            if isinstance(state.get("requirement_spec"), dict)
            else {}
        ),
        fallback="database",
    )
    existing_spec = state.get("requirement_spec")
    if isinstance(existing_spec, dict):
        existing_spec = apply_authoritative_datasource_type(
            existing_spec, datasource_type
        )
    interaction = _application_planning_interaction(state)
    application_planning_scope = state.get("workflow_scope") == "application_planning"
    request = _request_for_requirement_node(state, interaction)
    # 处理前一轮权限配置冲突的用户选择；解决后才允许继续生成需求草稿。
    conflict = state.get("authorization_config_conflict")
    conflict_resolved = False
    if (
        application_planning_scope
        and isinstance(conflict, dict)
        and conflict.get("requested") is True
    ):
        resolution = _resolve_authorization_config_conflict(
            state,
            interaction,
            conflict,
            existing_spec if isinstance(existing_spec, dict) else {},
        )
        if resolution.get("result") is not None:
            return resolution["result"]
        request = str(resolution["request"])
        conflict_resolved = True
    revision_requested = (
        interaction.get("action") == "revise"
        if application_planning_scope
        else _requirement_revision_requested(request)
    )
    clarification_round = _clarification_round(state)
    if not isinstance(existing_spec, dict) or (
        revision_requested
        and existing_spec.get("confirmation_status") != "pending_user_input"
    ):
        # 新需求或已确认需求的重新修订都开启新的三轮澄清预算。
        clarification_round = 0
    if (
        application_planning_scope
        and isinstance(existing_spec, dict)
        and existing_spec.get("confirmation_status") == "confirmed"
        and (
            not _has_application_planning_revision_context(state)
            and (not interaction or interaction.get("action") == "confirm")
        )
    ):
        # 已确认需求且没有新的结构化交互或修订游标时直接复用 checkpoint，禁止再次调用 LLM。
        return _confirmed_requirement_spec_update(state, existing_spec)
    if (
        existing_spec
        and existing_spec.get("confirmation_status") == "pending_user_confirmation"
        and not _has_explicit_user_submission(state)
    ):
        draft_path = str(
            state.get("requirement_spec_path")
            or requirement_spec_draft_markdown_path(state)
        )
        draft_json_path = str(
            state.get("requirement_spec_json_path")
            or requirement_spec_draft_json_path(state)
        )
        return {
            "phase": "requirements",
            "status": "requires_user_input",
            "requirement_spec": existing_spec,
            "requirements_confirmed": False,
            "requirements_clarification_round": clarification_round,
            "requirement_spec_path": draft_path,
            "requirement_spec_json_path": draft_json_path,
            "clarification": _requirement_spec_draft_payload(existing_spec),
            "timeline": ["requirements"],
        }
    if (
        existing_spec
        and existing_spec.get("confirmation_status") == "pending_user_confirmation"
        and not revision_requested
        and (
            interaction.get("action") == "confirm"
            if application_planning_scope
            else _user_confirmed_requirement_spec(request)
        )
    ):
        return confirm_requirement_spec_artifact(
            state,
            existing_spec,
            datasource_type=datasource_type,
        )
    analysis_request = _requirement_analysis_request(
        request,
        existing_spec,
        interaction,
        application_planning_scope=application_planning_scope,
    )
    analysis = analyze_requirements_with_chat_model(
        analysis_request,
        existing_spec=existing_spec,
        datasource_type=datasource_type,
        clarification_round=clarification_round,
        on_token=_llm_token_callback,
    )
    model_conflict = analysis.get("authorization_config_conflict")
    if (
        application_planning_scope
        and isinstance(model_conflict, dict)
        and model_conflict.get("requested") is True
    ):
        return _authorization_config_conflict_result(
            apply_authoritative_datasource_type(
                analysis["requirement_spec"],
                datasource_type,
            ),
            state,
            model_conflict,
        )
    spec = apply_authoritative_datasource_type(
        analysis["requirement_spec"],
        datasource_type,
    )
    _apply_menus_root_path_to_pages(spec, state)
    clarification = analysis["clarification"]
    clarification = _without_technical_datasource_questions(clarification, spec)
    clarification = _without_non_substantive_completeness_questions(
        clarification,
        spec,
    )
    _clear_unselected_initial_admin(spec, existing_spec)
    answered_authorization_questions = _apply_authorization_business_answers(
        spec,
        interaction,
    )
    clarification = _remove_answered_authorization_questions(
        clarification,
        answered_authorization_questions,
        spec,
    )
    authorization_errors = validate_authorization_requirements(
        spec,
        require_initial_admin=False,
    )
    next_authorization_question = _next_authorization_business_question(
        spec,
        answered_question_ids=answered_authorization_questions,
    )
    if authorization_errors or next_authorization_question is not None:
        # 权限业务语义不完整时不能写草稿或进入确认；按业务维度逐步引导用户补充。
        clarification = _authorization_validation_clarification(
            clarification,
            spec,
            authorization_errors,
            answered_authorization_questions,
        )
        spec["clarification_questions"] = clarification["questions"]
        spec["clarification_status"] = "requires_user_input"
        spec["confirmation_status"] = "pending_user_input"
        return {
            "phase": "requirements",
            "status": "requires_user_input",
            "requirement_spec": spec,
            "requirements_confirmed": False,
            "requirements_clarification_round": min(
                clarification_round + 1,
                MAX_REQUIREMENT_CLARIFICATION_ROUNDS,
            ),
            "requirement_spec_path": "",
            "requirement_spec_json_path": "",
            "clarification": clarification,
            "authorization_config_conflict": {},
            "timeline": ["requirements"],
        }
    next_clarification_round = clarification_round
    if _should_suppress_repeat_clarification(existing_spec, clarification):
        clarification = clear_clarification(spec)
        spec["clarification_questions"] = []
        spec["clarification_status"] = "clear"
    if clarification["status"] == "clear":
        readiness_errors = validate_requirement_spec_confirmation_readiness(spec)
        if readiness_errors:
            raise ValueError(
                "需求 AI 返回的 RequirementSpec 未达到确认条件："
                + "；".join(readiness_errors)
            )
        clarification = _requirement_spec_draft_payload(spec)
        spec["clarification_questions"] = []
        spec["clarification_status"] = "clear"
        spec["confirmation_status"] = "pending_user_confirmation"
        status = clarification["status"]
    else:
        next_round = clarification_round + 1
        if clarification_round >= MAX_REQUIREMENT_CLARIFICATION_ROUNDS:
            # 用户已经回答完第三轮后，不再信任模型继续追问，直接进入正式需求确认。
            readiness_errors = validate_requirement_spec_confirmation_readiness(spec)
            if readiness_errors:
                raise ValueError(
                    "需求 AI 最终合并的 RequirementSpec 未达到确认条件："
                    + "；".join(readiness_errors)
                )
            clarification = _requirement_spec_draft_payload(
                spec,
                clarification_limit_reached=True,
            )
            spec["clarification_questions"] = []
            spec["clarification_status"] = "clear"
            spec["confirmation_status"] = "pending_user_confirmation"
            status = clarification["status"]
        else:
            # 当前问题属于本轮最后一批；先展示给用户，下一次恢复只允许做最终合并。
            next_clarification_round = min(
                next_round,
                MAX_REQUIREMENT_CLARIFICATION_ROUNDS,
            )
            spec["confirmation_status"] = "pending_user_input"
            status = clarification["status"]

    if spec.get("confirmation_status") == "pending_user_input":
        # ask_user 期间只保留内存中的未完成事实，不能生成需求文档、页面占位或本地草稿。
        return {
            "phase": "requirements",
            "status": status,
            "requirement_spec": spec,
            "requirements_confirmed": False,
            "requirements_clarification_round": next_clarification_round,
            "requirement_spec_path": "",
            "requirement_spec_json_path": "",
            "clarification": clarification,
            "authorization_config_conflict": {},
            "timeline": ["requirements"],
        }

    # 澄清已结束后才生成待确认草稿；用户确认时再由确认分支提升为正式文档。
    spec_path = write_requirement_spec_draft_document(state, spec)
    return {
        "phase": "requirements",
        "status": status,
        "requirement_spec": spec,
        "requirements_confirmed": False,
        "requirements_clarification_round": next_clarification_round,
        "requirement_spec_path": spec_path,
        "requirement_spec_json_path": str(requirement_spec_draft_json_path(state)),
        "clarification": clarification,
        "authorization_config_conflict": (
            {} if conflict_resolved else state.get("authorization_config_conflict", {})
        ),
        "timeline": ["requirements"],
    }


def _authorization_config_answer_text(value: object) -> str:
    """读取选择题或文本题中的权限配置回答。"""

    if isinstance(value, dict):
        selected = value.get("selected")
        if isinstance(selected, list):
            return ",".join(str(item).strip() for item in selected if str(item).strip())
        return str(selected or value.get("other") or "").strip()
    return str(value or "").strip()


def _authorization_subjects(value: object) -> list[str]:
    """从管理员回答中拆分 subjectId，并保持首次出现顺序。"""

    subjects: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[,，;；\n]+", _authorization_config_answer_text(value)):
        subject = item.strip()
        if subject and subject not in seen:
            seen.add(subject)
            subjects.append(subject)
    return subjects


def _authorization_config_conflict_result(
    spec: dict,
    state: ProjectState,
    conflict: dict,
    *,
    collecting_admin: bool = False,
    message: str | None = None,
) -> dict:
    """构造配置冲突前置澄清，禁止在此之前写入 RequirementSpec 草稿。"""

    workspace = str(state.get("workspace") or "").strip()
    can_enable = bool(workspace) and authorization_configuration_can_enable(workspace)
    if collecting_admin:
        questions = [
            {
                "id": "authorization_initial_admin",
                "header": "初始管理员",
                "dimension": "内置权限初始化",
                "question": "请输入认证系统中真实存在或可预配置的初始管理员 subjectId；多个值请用逗号分隔。",
                "type": "text",
                "placeholder": "例如 user@example.com",
            }
        ]
    else:
        options = [
            {
                "label": "移除权限要求",
                "value": "remove",
                "description": "保持当前关闭权限配置，并移除本轮业务权限要求。",
            }
        ]
        if can_enable:
            options.insert(
                0,
                {
                    "label": "启用权限控制",
                    "value": "enable",
                    "description": "使用数据库并补充真实管理员 subjectId 后继续需求分析。",
                },
            )
        questions = [
            {
                "id": "authorization_config_decision",
                "header": "权限配置冲突",
                "dimension": "权限开关与业务需求",
                "question": "业务描述提出了权限控制，但当前应用配置未启用权限。请选择处理方式。",
                "type": "choice",
                "multiSelect": False,
                "allowOther": False,
                "options": options,
            }
        ]
    clarification = {
        "mode": "authorization_configuration_conflict",
        "status": "requires_user_input",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": questions,
        "message": message
        or "请先解决应用权限配置与业务需求之间的冲突，再继续生成 RequirementSpec。",
        "conflictEvidence": conflict.get("evidence", []),
    }
    spec["confirmation_status"] = "pending_user_input"
    spec["clarification_status"] = "requires_user_input"
    spec["clarification_questions"] = questions
    return {
        "phase": "requirements",
        "status": "requires_user_input",
        "requirement_spec": spec,
        "requirements_confirmed": False,
        "requirements_clarification_round": _clarification_round(state),
        "requirement_spec_path": "",
        "requirement_spec_json_path": "",
        "authorization_config_conflict": {
            **conflict,
            "decision": "enable" if collecting_admin else "",
        },
        "clarification": clarification,
        "timeline": ["requirements"],
    }


def _resolve_authorization_config_conflict(
    state: ProjectState,
    interaction: dict,
    conflict: dict,
    existing_spec: dict,
) -> dict:
    """处理前置澄清答案；只有管理员校验通过后才原子启用配置。"""

    answers = interaction.get("answers") if isinstance(interaction, dict) else {}
    answers = answers if isinstance(answers, dict) else {}
    decision = _authorization_config_answer_text(
        answers.get("authorization_config_decision")
    )
    decision = decision or str(conflict.get("decision") or "").strip()
    workspace = str(state.get("workspace") or "").strip()
    if decision == "enable":
        if not authorization_configuration_can_enable(workspace):
            return {
                "result": _authorization_config_conflict_result(
                    existing_spec,
                    state,
                    conflict,
                    message="当前应用不是数据库数据源，不能启用内置权限；请选择移除权限要求。",
                )
            }
        subjects = _authorization_subjects(answers.get("authorization_initial_admin"))
        if not subjects:
            return {
                "result": _authorization_config_conflict_result(
                    existing_spec,
                    state,
                    conflict,
                    collecting_admin=True,
                )
            }
        try:
            persist_authorization_configuration(
                workspace,
                initial_administrator_subjects=subjects,
            )
        except ApplicationAuthorizationConfigError as exc:
            raise ValueError(str(exc)) from exc
        return {
            "request": "\n".join(
                [
                    "权限配置冲突已确认解决：启用权限控制。",
                    "涉及权限控制：是。",
                    f"初始管理员成员标识：{'、'.join(subjects)}。",
                    str(interaction.get("request") or state.get("request") or ""),
                ]
            )
        }
    if decision == "remove":
        return {
            "request": "\n".join(
                [
                    "权限配置冲突已确认解决：移除本轮业务权限要求。",
                    "涉及权限控制：否。",
                    str(interaction.get("request") or state.get("request") or ""),
                ]
            )
        }
    return {
        "result": _authorization_config_conflict_result(existing_spec, state, conflict)
    }


def _requirement_spec_draft_payload(
    spec: dict,
    *,
    clarification_limit_reached: bool = False,
) -> dict:
    """构造已完成澄清的草稿载荷，联合确认由 ProductPlan 生成完成后统一处理。"""

    payload = clear_clarification(spec)
    payload["mode"] = "requirement_document_draft"
    payload["message"] = "需求事实已完成校验，正在生成同一阶段的页面与操作规划。"
    if clarification_limit_reached:
        payload["clarification_limit_reached"] = True
    payload["spec_summary"] = spec.get("app_info", {}).get("name", "未命名应用")
    return payload


def _authorization_validation_clarification(
    clarification: dict,
    spec: dict,
    errors: list[str],
    answered_question_ids: set[str] | None = None,
) -> dict:
    """把权限候选缺口转换为一次聚焦的业务澄清，不暴露结构字段错误。"""

    if any("DATA_AUTHORIZATION_NOT_SUPPORTED" in error for error in errors):
        issues = spec.get("authorization_capability_issues")
        issues = issues if isinstance(issues, list) else []
        issue_refs = [
            ref
            for issue in issues
            if isinstance(issue, dict)
            for ref in issue.get("sourceRefs", [])
            if isinstance(ref, str) and ref.strip()
        ]
        return {
            "mode": "authorization_capability_not_supported",
            "status": "requires_user_input",
            "questions": [],
            "message": "当前第一阶段不支持数据权限。请移除该授权要求，或将其明确改写为不承担授权语义的固定业务查询后重新生成需求文档。",
            "capabilityIssues": [
                {
                    "code": "DATA_AUTHORIZATION_NOT_SUPPORTED",
                    "capability": "data_authorization",
                    "sourceRefs": issue_refs,
                }
            ],
        }

    question = _next_authorization_business_question(
        spec,
        answered_question_ids=answered_question_ids or set(),
    )
    if question is None:
        # 只在候选形状异常等极少数情况使用兜底问题，仍然只要求业务说明。
        question = {
            "id": "authorization_business_review",
            "header": "权限业务梳理",
            "dimension": "权限业务梳理",
            "question": (
                "请先用业务语言说明需要控制的页面、操作或数据范围；"
                "如果没有对应的权限控制，请明确回答“无”。"
            ),
            "type": "text",
        }
    existing_questions = clarification.get("questions")
    questions = (
        [item for item in existing_questions if isinstance(item, dict)]
        if isinstance(existing_questions, list)
        else []
    )
    if not any(item.get("id") == question["id"] for item in questions):
        questions.append(question)
    return {
        **clarification,
        "mode": "ask_user_question",
        "status": "requires_user_input",
        "questions": questions,
        "message": "请先完成权限业务梳理，再确认需求文档。",
    }


def _clear_unselected_initial_admin(spec: dict, existing_spec: dict | None) -> None:
    """首次角色提取只保留职责事实，等待结构化选择后才写入系统管理员属性。"""

    existing_authorization = (
        existing_spec.get("authorization_requirements")
        if isinstance(existing_spec, dict)
        and isinstance(existing_spec.get("authorization_requirements"), dict)
        else {}
    )
    if str(existing_authorization.get("initialAdminRoleId") or "").strip():
        return
    authorization = spec.get("authorization_requirements")
    if not isinstance(authorization, dict) or authorization.get("enabled") is not True:
        return
    authorization.pop("initialAdminRoleId", None)
    roles = spec.get("user_roles")
    if not isinstance(roles, list):
        return
    for role in roles:
        if isinstance(role, dict):
            role["isSystemRole"] = False
            role["isInitialAdminRole"] = False


def _next_authorization_business_question(
    spec: dict,
    *,
    answered_question_ids: set[str] | None = None,
) -> dict | None:
    """按页面和操作顺序返回一个权限业务梳理问题。"""

    authorization = spec.get("authorization_requirements")
    if not isinstance(authorization, dict) or authorization.get("enabled") is not True:
        return None
    answered_question_ids = answered_question_ids or set()

    restricted_pages = authorization.get("restrictedPages")
    if (
        "authorization_page_business" not in answered_question_ids
        and isinstance(restricted_pages, list)
        and any(
            not isinstance(item, dict)
            or not str(item.get("name") or "").strip()
            or not str(item.get("description") or "").strip()
            for item in restricted_pages
        )
    ):
        return {
            "id": "authorization_page_business",
            "header": "权限业务梳理 1/2",
            "dimension": "受控页面业务含义",
            "question": (
                "第 1 步，请说明哪些业务页面或业务对象需要限制访问，以及限制的业务原因。"
                "如果不需要页面级权限控制，请回答“无”。"
            ),
            "type": "text",
        }

    restricted_operations = authorization.get("restrictedOperations")
    if (
        "authorization_operation_business" not in answered_question_ids
        and isinstance(restricted_operations, list)
        and any(
            not isinstance(item, dict)
            or not str(item.get("name") or "").strip()
            or not str(item.get("description") or "").strip()
            for item in restricted_operations
        )
    ):
        return {
            "id": "authorization_operation_business",
            "header": "权限业务梳理 2/2",
            "dimension": "受控操作业务含义",
            "question": (
                "第 2 步，请说明哪些业务操作需要授权，以及为什么需要限制。"
                "如果不需要操作级权限控制，请回答“无”。"
            ),
            "type": "text",
        }

    roles = spec.get("user_roles")
    roles = roles if isinstance(roles, list) else []
    role_options = [
        {
            "label": str(role.get("name") or role.get("id") or "未命名角色"),
            "value": str(role.get("id") or "").strip(),
        }
        for role in roles
        if isinstance(role, dict) and str(role.get("id") or "").strip()
    ]
    if not role_options and "authorization_business_roles" not in answered_question_ids:
        return {
            "id": "authorization_business_roles",
            "header": "业务角色梳理",
            "dimension": "业务参与者",
            "question": (
                "需求中尚未识别出业务角色。请先说明应用有哪些业务参与者，以及是否存在管理员类角色；"
                "确认后再选择谁承担系统权限管理。"
            ),
            "type": "text",
        }
    initial_admin_role_id = str(authorization.get("initialAdminRoleId") or "").strip()
    selected_initial_roles = [
        role
        for role in roles
        if isinstance(role, dict) and role.get("isInitialAdminRole") is True
    ]
    if "authorization_initial_admin_role" not in answered_question_ids and (
        len(selected_initial_roles) != 1
        or not initial_admin_role_id
        or str(selected_initial_roles[0].get("id") or "").strip()
        != initial_admin_role_id
        or selected_initial_roles[0].get("isSystemRole") is not True
    ):
        return {
            "id": "authorization_initial_admin_role",
            "header": "初始系统管理员",
            "dimension": "系统权限管理角色",
            "question": (
                "已识别业务角色："
                + "、".join(str(option["label"]) for option in role_options)
                + "。请选择其中一个首次承担系统权限管理的角色；"
                "如这些业务角色都不承担该职责，再选择新建独立系统管理员。"
            ),
            "type": "choice",
            "allowOther": False,
            "options": [
                *role_options,
                {
                    "label": "新建独立系统管理员",
                    "value": "__create_system_administrator__",
                },
            ],
        }

    for field_name, label in (
        ("restrictedPages", "受控页面"),
        ("restrictedOperations", "受控操作"),
    ):
        items = authorization.get(field_name)
        items = items if isinstance(items, list) else []
        for item in items:
            if not isinstance(item, dict) or item.get("defaultGrantedRoleIds"):
                continue
            rule_id = str(item.get("ruleId") or "").strip()
            if not rule_id:
                continue
            question_id = f"authorization_default_grants_{rule_id}"
            if question_id in answered_question_ids:
                continue
            return {
                "id": question_id,
                "header": "默认角色授权",
                "dimension": label,
                "question": f"首次初始化时，哪些业务角色默认拥有“{item.get('name') or label}”权限？可多选。",
                "type": "choice",
                "multiSelect": True,
                "allowOther": False,
                "options": role_options,
            }

    return None


_AUTHORIZATION_BUSINESS_QUESTION_TARGETS = {
    "authorization_page_business": {
        "field": "restrictedPages",
        "fallback_name": "受控业务页面",
    },
    "authorization_operation_business": {
        "field": "restrictedOperations",
        "fallback_name": "受控业务操作",
    },
}


def _authorization_answer_text(value: object) -> str:
    """把权限澄清卡的结构化回答还原为一段业务说明文本。"""

    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        selected = value.get("selected")
        other = str(value.get("other") or "").strip()
        selected_text = _authorization_answer_text(selected)
        return "；".join(
            item
            for item in (
                f"已选：{selected_text}" if selected_text else "",
                f"其他补充：{other}" if other else "",
            )
            if item
        )
    return str(value or "").strip()


def _authorization_answer_values(value: object) -> list[str]:
    """提取选择题的稳定选项值，供角色选择与默认授权关系写回。"""

    if isinstance(value, dict):
        return _authorization_answer_values(value.get("selected"))
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _is_explicit_no_authorization_answer(value: str) -> bool:
    """识别权限澄清协议约定的否定回答，不解释自由业务语言。"""

    normalized = value.replace(" ", "").replace("已选：", "")
    return normalized in {
        "无",
        "暂无",
        "没有",
        "不需要",
        "无需",
        "否",
        "不用",
    }


def _authorization_candidate_is_complete(field: str, value: object) -> bool:
    """判断一个权限候选是否已经具备需求阶段的业务语义。"""

    if not isinstance(value, dict):
        return False
    return bool(
        str(value.get("name") or "").strip()
        and str(value.get("description") or "").strip()
    )


def _apply_authorization_business_answers(
    spec: dict,
    interaction: dict,
) -> set[str]:
    """按稳定问题 ID 合并权限澄清回答，保留用户原话而不解析业务关键词。"""

    answers = interaction.get("answers") if isinstance(interaction, dict) else None
    authorization = spec.get("authorization_requirements")
    if not isinstance(answers, dict) or not isinstance(authorization, dict):
        return set()
    if authorization.get("enabled") is not True:
        return set()

    updated_authorization = deepcopy(authorization)
    answered_question_ids: set[str] = set()
    for question_id, target in _AUTHORIZATION_BUSINESS_QUESTION_TARGETS.items():
        if question_id not in answers:
            continue
        answer_text = _authorization_answer_text(answers.get(question_id))
        if not answer_text:
            continue
        answered_question_ids.add(question_id)
        field = str(target["field"])
        if _is_explicit_no_authorization_answer(answer_text):
            updated_authorization[field] = []
            continue

        raw_items = updated_authorization.get(field)
        items = raw_items if isinstance(raw_items, list) else []
        complete_items = [
            deepcopy(item)
            for item in items
            if _authorization_candidate_is_complete(field, item)
        ]
        if complete_items:
            source_ref = f"用户权限澄清回答：{answer_text}"
            for item in complete_items:
                source_refs = item.get("sourceRefs")
                if not isinstance(source_refs, list):
                    source_refs = []
                if source_ref not in source_refs:
                    source_refs.append(source_ref)
                item["sourceRefs"] = source_refs
            updated_authorization[field] = complete_items
            continue

        seed_item = next(
            (item for item in items if isinstance(item, dict)),
            {},
        )
        source_ref = f"用户权限澄清回答：{answer_text}"
        source_refs = (
            [
                str(item).strip()
                for item in seed_item.get("sourceRefs", [])
                if str(item).strip()
            ]
            if isinstance(seed_item.get("sourceRefs"), list)
            else []
        )
        if source_ref not in source_refs:
            source_refs.append(source_ref)

        fallback_item = {
            "ruleId": str(seed_item.get("ruleId") or "").strip(),
            "name": str(seed_item.get("name") or target["fallback_name"]).strip(),
            "description": str(seed_item.get("description") or answer_text).strip(),
            "rationale": str(seed_item.get("rationale") or answer_text).strip(),
            "sourceRefs": source_refs,
        }
        updated_authorization[field] = [fallback_item]

    if "authorization_initial_admin_role" in answers:
        selected = _authorization_answer_values(
            answers["authorization_initial_admin_role"]
        )
        selected_role_id = selected[0] if len(selected) == 1 else ""
        roles = spec.get("user_roles")
        roles = deepcopy(roles) if isinstance(roles, list) else []
        if selected_role_id == "__create_system_administrator__":
            used_ids = {
                str(role.get("id") or "").strip()
                for role in roles
                if isinstance(role, dict)
            }
            selected_role_id = "system_administrator"
            suffix = 2
            while selected_role_id in used_ids:
                selected_role_id = f"system_administrator_{suffix}"
                suffix += 1
            roles.append(
                {
                    "id": selected_role_id,
                    "name": "系统管理员",
                    "description": "首次负责系统权限管理的角色。",
                    "isSystemRole": True,
                    "isInitialAdminRole": True,
                }
            )
        if selected_role_id and any(
            isinstance(role, dict)
            and str(role.get("id") or "").strip() == selected_role_id
            for role in roles
        ):
            for role in roles:
                if not isinstance(role, dict):
                    continue
                is_selected = str(role.get("id") or "").strip() == selected_role_id
                role["isInitialAdminRole"] = is_selected
                role["isSystemRole"] = is_selected or bool(role.get("isSystemRole"))
            spec["user_roles"] = roles
            updated_authorization["initialAdminRoleId"] = selected_role_id
            answered_question_ids.add("authorization_initial_admin_role")

    for question_id, answer in answers.items():
        if not str(question_id).startswith("authorization_default_grants_"):
            continue
        rule_id = str(question_id).removeprefix("authorization_default_grants_")
        selected_role_ids = _authorization_answer_values(answer)
        if not selected_role_ids:
            continue
        for field_name in ("restrictedPages", "restrictedOperations"):
            items = updated_authorization.get(field_name)
            if not isinstance(items, list):
                continue
            for item in items:
                if (
                    isinstance(item, dict)
                    and str(item.get("ruleId") or "").strip() == rule_id
                ):
                    item["defaultGrantedRoleIds"] = selected_role_ids
                    answered_question_ids.add(str(question_id))

    if answered_question_ids:
        spec["authorization_requirements"] = updated_authorization
    return answered_question_ids


def _remove_answered_authorization_questions(
    clarification: dict,
    answered_question_ids: set[str],
    spec: dict,
) -> dict:
    """从下一轮问题中移除本轮已回答的权限维度，避免原生中断重复展示。"""

    if not answered_question_ids:
        return clarification
    questions = clarification.get("questions")
    if not isinstance(questions, list):
        return clarification
    remaining = [
        item
        for item in questions
        if not isinstance(item, dict) or item.get("id") not in answered_question_ids
    ]
    if remaining:
        return {
            **clarification,
            "questions": remaining,
            "status": "requires_user_input",
        }
    return clear_clarification(spec)


def _requirement_spec_confirmed_payload(spec: dict) -> dict:
    return {
        "mode": "requirement_document_confirmation",
        "status": "clear",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": [],
        "assumptions": [],
        "message": "需求文档已确认，可以继续后续规划。",
        "spec_summary": spec.get("app_info", {}).get("name", "未命名应用"),
    }


def _user_confirmed_requirement_spec(request: str) -> bool:
    if user_requested_changes_text(request):
        return False
    return user_confirmed_text(
        request,
        positive_signals=(
            "正确",
            "没问题",
            "继续规划",
            "可以继续",
            "无误",
            "确认",
            "好的",
            "好",
            "OK",
            "ok",
        ),
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
            "不好",
        ),
    )


def _application_planning_interaction(state: ProjectState) -> dict:
    """读取当前创建规划的结构化动作，其他 workflow 不走该分支。"""

    value = state.get("application_planning_interaction")
    return value if isinstance(value, dict) and value else {}


def _request_for_requirement_node(
    state: ProjectState,
    interaction: dict,
) -> str:
    """为需求节点选择结构化交互中的明确请求，避免用阶段文本猜动作。"""

    if state.get("workflow_scope") == "application_planning" and interaction:
        return str(interaction.get("request") or "").strip()
    return str(state.get("request") or "")


def _has_application_planning_revision_context(state: ProjectState) -> bool:
    """判断创建规划 checkpoint 是否带有尚未消费的修订上下文。"""

    return any(
        bool(state.get(field))
        for field in (
            "design_change_submission",
            "design_change_request",
            "design_change_generation_target",
            "design_change_generation_request",
            "design_interaction_origin",
        )
    )


def _confirmed_requirement_spec_update(
    state: ProjectState,
    spec: dict,
) -> dict:
    """构造已确认需求的早退状态，不触发文档重写或模型分析。"""

    datasource_type = datasource_type_from_artifact(spec, fallback="database")
    confirmed_spec = apply_authoritative_datasource_type(spec, datasource_type)
    return {
        "phase": "requirements",
        "status": "completed",
        "requirement_spec": confirmed_spec,
        "requirements_confirmed": True,
        "requirements_clarification_round": 0,
        "requirement_spec_path": str(state.get("requirement_spec_path") or ""),
        "requirement_spec_json_path": str(
            state.get("requirement_spec_json_path") or ""
        ),
        "clarification": _requirement_spec_confirmed_payload(confirmed_spec),
        "timeline": ["requirements"],
    }


def _has_explicit_user_submission(state: ProjectState) -> bool:
    """创建规划只接受原生中断恢复写入的显式交互，其他调用保持原行为。"""

    return state.get("workflow_scope") != "application_planning" or bool(
        state.get("application_planning_interaction")
    )


def _requirement_revision_requested(request: str) -> bool:
    """只根据本轮确认答案判断是否需要修订需求。"""

    return user_requested_changes_text(request)


def _requirement_analysis_request(
    request: str,
    existing_spec: dict | None,
    interaction: dict | None = None,
    *,
    application_planning_scope: bool = False,
) -> str:
    """把本轮待确认文档上的修改性意见提升为需求修订请求。"""

    if application_planning_scope:
        # application_planning 的分支已由审阅门明确给出，原文不能再经过关键词分类。
        return request
    revision_feedback = extract_confirmation_answer(request).strip() or request
    if (
        not isinstance(existing_spec, dict)
        or existing_spec.get("confirmation_status") != "pending_user_confirmation"
        or not user_requested_changes_text(request)
    ):
        return request
    return "\n".join(
        [
            "用户正在审核已生成的需求文档，并提出了以下修改意见。",
            "请基于现有 RequirementSpec 和这段最新意见重新生成完整需求文档。",
            "最新修改意见优先覆盖冲突的旧需求；不要把本次意见当作确认通过。",
            "",
            "用户修改意见：",
            revision_feedback,
        ]
    )


def _should_suppress_repeat_clarification(
    existing_spec: dict | None,
    clarification: dict,
) -> bool:
    if not isinstance(existing_spec, dict):
        return False
    if existing_spec.get("confirmation_status") != "pending_user_input":
        return False
    if clarification.get("status") != "requires_user_input":
        return False

    questions = clarification.get("questions")
    if not isinstance(questions, list) or not questions:
        return False

    return all(_is_optional_additive_question(question) for question in questions)


def _apply_menus_root_path_to_pages(spec: dict, state: ProjectState) -> None:
    """从 application.json 读取 menus.rootPath 并拼接到所有页面路由前。"""
    try:
        app_file = workspace_root(state) / ".xcodeagent" / "application.json"
        if not app_file.is_file():
            return
        app_config = json.loads(app_file.read_text(encoding="utf-8"))
        root_path = str(
            (app_config.get("menus") or {}).get("rootPath", "") or "/"
        ).strip()
        menus_enabled = bool((app_config.get("menus") or {}).get("enable"))
    except Exception:
        return

    app_info = spec.get("app_info") if isinstance(spec.get("app_info"), dict) else {}
    app_info["menu_enabled"] = menus_enabled
    if not root_path or root_path == "/":
        spec["app_info"] = app_info
        return
    root_path = root_path.rstrip("/")
    app_info["route_root_path"] = root_path
    spec["app_info"] = app_info
    for page in spec.get("pages", []):
        if isinstance(page, dict) and page.get("path"):
            page_path = str(page["path"]).strip()
            if menus_enabled and page_path == "/":
                page["path"] = root_path + _menu_home_leaf_path(page)
            elif page_path.startswith("/"):
                page["path"] = root_path + page_path
            else:
                page["path"] = root_path + "/" + page_path


def _menu_home_leaf_path(page: dict) -> str:
    """为启用菜单时的首页类页面生成非根路径的叶子路由。"""

    page_id = str(page.get("pageId") or page.get("id") or "").strip()
    route = re.sub(r"[^a-zA-Z0-9_-]+", "-", page_id or "home").strip("-_")
    route = route.replace("_", "-").lower() or "home"
    if route.endswith("-page") and route != "dashboard-page":
        route = route[: -len("-page")] or route
    if route in {"dashboard", "dashboard-page", "home", "index"}:
        route = "home"
    return f"/{route}"


def _is_optional_additive_question(question: object) -> bool:
    if not isinstance(question, dict):
        return False
    text = "".join(
        str(question.get(key) or "")
        for key in ("id", "header", "dimension", "question")
    )
    normalized = text.replace(" ", "")
    additive_markers = ("其他", "更多", "还有", "补充", "是否还", "是否有")
    requirement_dimensions = ("角色", "页面", "菜单", "功能", "模块", "验收")
    return any(marker in normalized for marker in additive_markers) and any(
        dimension in normalized for dimension in requirement_dimensions
    )


def _without_technical_datasource_questions(clarification: dict, spec: dict) -> dict:
    """移除产品需求阶段误生成的数据源技术问题，并在无产品问题时直接清空澄清。"""

    questions = clarification.get("questions")
    if not isinstance(questions, list):
        return clarification
    filtered = [
        question for question in questions if not _is_datasource_question(question)
    ]
    if not filtered:
        return clear_clarification(spec)
    return {**clarification, "questions": filtered}


def _without_non_substantive_completeness_questions(
    clarification: dict,
    spec: dict,
) -> dict:
    """忽略模型发出的泛化完整性确认，让正式产物确认卡承担确认职责。"""

    questions = clarification.get("questions")
    if not isinstance(questions, list):
        return clarification
    filtered = [
        question
        for question in questions
        if not _is_non_substantive_completeness_question(question)
    ]
    if not filtered:
        return clear_clarification(spec)
    return {**clarification, "questions": filtered}


def _is_non_substantive_completeness_question(question: object) -> bool:
    """识别“需求是否完整/无需进一步澄清”这类没有新增信息的问题。"""

    if not isinstance(question, dict):
        return False
    text = "".join(
        str(question.get(key) or "")
        for key in ("id", "header", "dimension", "question")
    )
    normalized = re.sub(r"[\s\u3000，。！？；：、,.!?;:]+", "", text).lower()
    generic_markers = (
        "请确认需求已完整无需进一步澄清",
        "请确认需求是否完整无需进一步澄清",
        "需求已完整无需进一步澄清",
        "需求是否完整无需进一步澄清",
        "无需进一步澄清",
        "不需要进一步澄清",
        "是否还需要进一步澄清",
        "是否还需要澄清",
    )
    return any(marker in normalized for marker in generic_markers)


def _is_datasource_question(question: object) -> bool:
    """识别数据源、数据库、存储与持久化类技术澄清问题。"""

    if not isinstance(question, dict):
        return False
    text = "".join(
        str(question.get(key) or "")
        for key in ("id", "header", "dimension", "question")
    ).replace(" ", "")
    return any(marker in text for marker in ("数据源", "数据库", "存储方式", "持久化"))
