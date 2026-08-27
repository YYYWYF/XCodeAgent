from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from hashlib import sha256
from typing import Any, Callable

from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.data_source_policy import DatasourceType
from app.services.model_transport_retry import run_with_transport_retry
from app.services.requirement_spec import (
    _authorization_enabled_from_request,
    create_requirement_spec,
)
from app.tools.ask_user import (
    ask_user,
    extract_ask_user_clarification,
)
from app.utils.model_output import (
    extract_json_object,
    repair_unescaped_json_string_quotes,
)

logger = logging.getLogger("uvicorn.error")

MAX_REQUIREMENT_CLARIFICATION_ROUNDS = 3
MIN_REQUIREMENT_CLARIFICATION_QUESTIONS = 5
MAX_REQUIREMENT_CLARIFICATION_QUESTIONS = 8
_AUTHORIZATION_FACT_EXTRACTION_ATTEMPTS = 3
_LOWER_SNAKE_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _authorization_fact_extraction_prompt(
    request: str,
    existing_spec: dict[str, Any] | None,
    pages: list[dict[str, Any]],
) -> str:
    """构造只提取角色与权限业务事实的 JSON 提示，禁止把结构缺口转成用户追问。"""

    existing_roles = (
        existing_spec.get("user_roles")
        if isinstance(existing_spec, dict)
        and isinstance(existing_spec.get("user_roles"), list)
        else []
    )
    page_candidates = [
        {
            "pageId": str(page.get("pageId") or "").strip(),
            "name": str(page.get("name") or "").strip(),
            "description": str(page.get("description") or "").strip(),
        }
        for page in pages
        if isinstance(page, dict) and str(page.get("pageId") or "").strip()
    ]
    return (
        "You extract explicit authorization business facts for an application requirement. "
        "Return exactly one JSON object without markdown and never call tools. The root must contain exactly "
        "user_roles and authorization_requirements. Do not add unknown fields.\n"
        "user_roles is an array of every explicitly stated business participant. Each item must contain exactly "
        "id, name, description. id must be lower_snake_case; description must state the role's explicit business "
        "responsibilities. Return [] only if the request truly identifies no business participant.\n"
        "authorization_requirements must contain exactly restrictedPages, restrictedOperations, dataAuthorizationIssues. "
        "Return only controls explicitly stated in the request; empty arrays are valid. Every restrictedPages item "
        "must contain exactly name, targetPageId, description, rationale, sourceRefs, defaultGrantedRoleIds; every "
        "restrictedOperations item must contain exactly name, description, rationale, sourceRefs, defaultGrantedRoleIds. "
        "For each restrictedPages item, targetPageId must be copied exactly from one item in the supplied page "
        "catalogue. It is the stable identity of the controlled page, not a technical implementation detail. "
        "description states who can perform or access the business target; rationale states the business reason; "
        "sourceRefs is a non-empty string array citing the original request; defaultGrantedRoleIds is an array "
        "referencing user_roles ids. Return [] when the request does not explicitly state the default role grant; "
        "never guess a role. dataAuthorizationIssues contains only explicit data-authorization requests that V1 cannot "
        "implement. Each item must contain exactly description and sourceRefs. Add an issue when different members, roles, "
        "organizations, projects, customers, or other relations determine which records can be read, modified, or created. "
        "Do not add an issue for a fixed business query such as 'my applications' unless it is an authorization boundary. "
        "Never emit empty objects, placeholders, allowedRoles, resource keys, technical ids, routes, API fields, SQL, "
        "or initial-system-administrator fields.\n"
        "Do not ask the user to repeat facts already stated. If a fact is explicit, express it completely using the "
        "required fields.\n"
        f"Existing business roles, if any:\n{json.dumps(existing_roles, ensure_ascii=False)}\n\n"
        f"Confirmed page catalogue for restrictedPages.targetPageId:\n{json.dumps(page_candidates, ensure_ascii=False)}\n\n"
        f"Original requirement:\n{request}"
    )


def _is_lower_snake_case(value: Any) -> bool:
    """判断权限事实中的稳定角色和数据规则标识是否符合当前契约。"""

    return bool(_LOWER_SNAKE_CASE_PATTERN.fullmatch(str(value or "").strip()))


def _string_list(value: Any) -> list[str]:
    """将不可信列表收敛为非空字符串列表。"""

    return (
        [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, list)
        else []
    )


def _validate_authorization_fact_output(
    value: Any,
    pages: list[dict[str, Any]],
) -> list[str]:
    """在写入 RequirementSpec 前严格校验角色和权限业务事实的模型原始输出。"""

    if not isinstance(value, dict):
        return ["权限事实模型输出必须是 JSON 对象。"]
    errors: list[str] = []
    expected_root = {"user_roles", "authorization_requirements"}
    if set(value) != expected_root:
        errors.append(
            "权限事实模型输出根字段必须且只能包含 user_roles、authorization_requirements。"
        )
    roles = value.get("user_roles")
    if not isinstance(roles, list):
        errors.append("权限事实模型输出.user_roles 必须是数组。")
        roles = []
    role_ids: set[str] = set()
    for index, role in enumerate(roles):
        if not isinstance(role, dict) or set(role) != {"id", "name", "description"}:
            errors.append(f"权限事实模型输出.user_roles[{index}] 字段不符合契约。")
            continue
        role_id = str(role.get("id") or "").strip()
        if not _is_lower_snake_case(role_id) or role_id in role_ids:
            errors.append(
                f"权限事实模型输出.user_roles[{index}].id 必须唯一且为 lower_snake_case。"
            )
        role_ids.add(role_id)
        if (
            not str(role.get("name") or "").strip()
            or not str(role.get("description") or "").strip()
        ):
            errors.append(f"权限事实模型输出.user_roles[{index}] 缺少名称或职责。")
    authorization = value.get("authorization_requirements")
    if not isinstance(authorization, dict):
        return [*errors, "权限事实模型输出.authorization_requirements 必须是对象。"]
    expected_authorization = {
        "restrictedPages",
        "restrictedOperations",
        "dataAuthorizationIssues",
    }
    if set(authorization) != expected_authorization:
        errors.append("权限事实模型输出.authorization_requirements 字段不符合契约。")
    page_ids = {
        str(page.get("pageId") or "").strip()
        for page in pages
        if isinstance(page, dict) and str(page.get("pageId") or "").strip()
    }
    for field_name in ("restrictedPages", "restrictedOperations"):
        items = authorization.get(field_name)
        if not isinstance(items, list):
            errors.append(f"权限事实模型输出.{field_name} 必须是数组。")
            continue
        for index, item in enumerate(items):
            expected = (
                {
                    "name",
                    "targetPageId",
                    "description",
                    "rationale",
                    "sourceRefs",
                    "defaultGrantedRoleIds",
                }
                if field_name == "restrictedPages"
                else {
                    "name",
                    "description",
                    "rationale",
                    "sourceRefs",
                    "defaultGrantedRoleIds",
                }
            )
            if not isinstance(item, dict) or set(item) != expected:
                errors.append(
                    f"权限事实模型输出.{field_name}[{index}] 字段不符合契约。"
                )
                continue
            if any(
                not str(item.get(key) or "").strip()
                for key in ("name", "description", "rationale")
            ):
                errors.append(f"权限事实模型输出.{field_name}[{index}] 缺少业务语义。")
            if field_name == "restrictedPages":
                target_page_id = str(item.get("targetPageId") or "").strip()
                if not target_page_id:
                    errors.append(
                        f"权限事实模型输出.{field_name}[{index}] 缺少 targetPageId。"
                    )
                elif page_ids and target_page_id not in page_ids:
                    errors.append(
                        f"权限事实模型输出.{field_name}[{index}].targetPageId 未引用页面目录。"
                    )
            if not _string_list(item.get("sourceRefs")):
                errors.append(
                    f"权限事实模型输出.{field_name}[{index}] 缺少 sourceRefs。"
                )
            grants = _string_list(item.get("defaultGrantedRoleIds"))
            if any(role_id not in role_ids for role_id in grants):
                errors.append(
                    f"权限事实模型输出.{field_name}[{index}] 默认角色授权无效。"
                )
    data_issues = authorization.get("dataAuthorizationIssues")
    if not isinstance(data_issues, list):
        return [*errors, "权限事实模型输出.dataAuthorizationIssues 必须是数组。"]
    for index, item in enumerate(data_issues):
        expected = {"description", "sourceRefs"}
        if not isinstance(item, dict) or set(item) != expected:
            errors.append(
                f"权限事实模型输出.dataAuthorizationIssues[{index}] 字段不符合契约。"
            )
            continue
        if not str(item.get("description") or "").strip():
            errors.append(
                f"权限事实模型输出.dataAuthorizationIssues[{index}] 缺少业务说明。"
            )
        if not _string_list(item.get("sourceRefs")):
            errors.append(
                f"权限事实模型输出.dataAuthorizationIssues[{index}] 缺少 sourceRefs。"
            )
    return errors


def _extract_authorization_facts(
    request: str,
    existing_spec: dict[str, Any] | None,
    settings: Settings,
    pages: list[dict[str, Any]],
) -> dict[str, Any]:
    """独立提取权限业务事实，并在字段形状漂移时要求模型自动修复。"""

    feedback = ""
    for _attempt in range(_AUTHORIZATION_FACT_EXTRACTION_ATTEMPTS):
        prompt = _authorization_fact_extraction_prompt(request, existing_spec, pages)
        if feedback:
            prompt += (
                "\n\nPrevious output failed validation. Return a corrected complete JSON only:\n"
                + feedback
            )
        result = create_chat_model(settings).invoke(prompt)
        payload = extract_json_object(
            _coerce_content_text(getattr(result, "content", "")) or ""
        )
        errors = _validate_authorization_fact_output(payload, pages)
        if not errors:
            return payload
        feedback = "\n".join(f"- {error}" for error in errors[:12])
    raise ValueError("权限业务事实自动修复达到上限后仍未通过校验：" + feedback)


def _merge_authorization_facts(
    agent_spec: dict[str, Any] | None,
    facts: dict[str, Any],
    existing_spec: dict[str, Any] | None,
) -> dict[str, Any]:
    """把独立事实提取结果并入当前分析，防止澄清调用或缺字段清空已明确权限语义。"""

    merged = deepcopy(agent_spec) if isinstance(agent_spec, dict) else {}
    fact_roles = facts.get("user_roles")
    if isinstance(fact_roles, list) and fact_roles:
        # 原始需求中明确提到的角色是当前轮的权威事实，不能被历史草稿或通用兜底角色覆盖。
        merged["user_roles"] = fact_roles
    authorization = merged.get("authorization_requirements")
    authorization = deepcopy(authorization) if isinstance(authorization, dict) else {}
    fact_authorization = facts.get("authorization_requirements")
    fact_authorization = (
        fact_authorization if isinstance(fact_authorization, dict) else {}
    )
    for field_name in ("restrictedPages", "restrictedOperations"):
        fact_items = fact_authorization.get(field_name)
        if isinstance(fact_items, list):
            # 权限候选以独立事实提取为唯一来源，空数组也表示没有明确提出该维度。
            authorization[field_name] = fact_items
    data_issues = fact_authorization.get("dataAuthorizationIssues")
    if isinstance(data_issues, list):
        merged["authorization_capability_issues"] = [
            {
                "code": "DATA_AUTHORIZATION_NOT_SUPPORTED",
                "capability": "data_authorization",
                "description": str(item.get("description") or "").strip(),
                "sourceRefs": _string_list(item.get("sourceRefs")),
            }
            for item in data_issues
            if isinstance(item, dict)
            and str(item.get("description") or "").strip()
            and _string_list(item.get("sourceRefs"))
        ]
    if authorization:
        merged["authorization_requirements"] = authorization
    return merged


def _requirements_prompt(
    request: str,
    existing_spec: dict[str, Any] | None = None,
    datasource_type: DatasourceType = "database",
    clarification_round: int = 0,
    format_correction: bool = False,
) -> str:
    """构建产品需求提示；实体归技术规划阶段。"""

    bounded_round = max(
        0, min(clarification_round, MAX_REQUIREMENT_CLARIFICATION_ROUNDS)
    )
    next_round = min(
        bounded_round + 1,
        MAX_REQUIREMENT_CLARIFICATION_ROUNDS,
    )

    visible_existing_spec = (
        {
            key: value
            for key, value in existing_spec.items()
            if key not in {"data_sources", "acceptance_criteria"}
        }
        if isinstance(existing_spec, dict)
        else None
    )
    revision_context = (
        "Use the existing RequirementSpec and the latest user feedback together as inputs. Generate "
        "one new, complete RequirementSpec whose full contents authoritatively replace the old document; "
        "never return a partial patch. Treat the latest feedback as an incremental patch to the product, "
        "not as a replacement application request. Start from every existing product fact, apply "
        "only the additions, removals, or replacements explicitly requested by the feedback, and keep "
        "all unrelated fields semantically unchanged. The latest feedback overrides only directly "
        "conflicting older requirements. Preserve stable ids for unchanged items, remove items the user "
        "no longer wants, and add newly requested items. app_info.summary must summarize the complete "
        "merged application after the patch; it must not contain only the latest feedback.\n"
        f"Existing RequirementSpec:\n{json.dumps(visible_existing_spec, ensure_ascii=False)}\n\n"
        if visible_existing_spec
        else "Create a new RequirementSpec from the user request.\n"
    )
    round_context = (
        f"The user has already completed all {MAX_REQUIREMENT_CLARIFICATION_ROUNDS} clarification rounds. "
        f"This is the final consolidation pass after round {MAX_REQUIREMENT_CLARIFICATION_ROUNDS}; "
        "never call ask_user in this pass. Merge the latest answers into the best complete JSON and "
        "leave any still-optional detail empty.\n"
        if bounded_round >= MAX_REQUIREMENT_CLARIFICATION_ROUNDS
        else (
            f"This request is entering clarification round {next_round} of "
            f"{MAX_REQUIREMENT_CLARIFICATION_ROUNDS}; the number counts rounds already completed "
            f"before this model call ({bounded_round}) plus this call. At the final round, ask "
            "once for any still-missing required field; do not defer questions to a later round.\n"
        )
    )
    clarification_policy = (
        "Before asking the user, check these required fields in order: "
        "(1) app_info.name is non-empty, "
        "(2) user_roles has at least one role, "
        "(3) feature_modules has at least one module, "
        "(4) pages has at least one page, "
        "(5) business_flows has at least one flow. "
        "If any required field is missing or empty and cannot be directly inferred from the user's "
        "request, call ask_user once for exactly those missing fields. If all required fields are "
        "present, return the complete JSON immediately unless a permission ambiguity or the business-agent "
        "suitability check described above still requires user input. "
        "Do not classify gaps as material versus technical, do not count gaps, and do not deliberate "
        "about whether a gap is worth asking — just check the five fields above. "
        f"This workflow allows at most {MAX_REQUIREMENT_CLARIFICATION_ROUNDS} clarification rounds. "
        f"{round_context}"
        "Permission business questions are an explicit exception: ask only one permission "
        "category per turn so the user can progressively confirm page access, operations, and data scope. "
        "Do not ask open-ended follow-up questions such as whether there are more roles, pages, "
        "or optional features after the user has answered a prior clarification turn. "
        "Never use ask_user to ask whether the requirements are complete, whether the user has "
        "anything else to add, or to request generic confirmation. "
        "Except for the business-agent suitability check described above, omit optional details that the user did "
        "not request instead of inventing assumptions. "
        f"After the user has answered round {MAX_REQUIREMENT_CLARIFICATION_ROUNDS}, never call ask_user again "
        "under any circumstance; return the best complete JSON supported by the request and answers, "
        "leaving genuinely unspecified optional details empty for the confirmation UI.\n"
    )
    followup_policy = (
        "The latest request contains answers to a previous clarification turn. Treat these answers as "
        "the user's confirmation for the asked dimensions. Merge them into a complete RequirementSpec "
        "now. Do not call ask_user again for the same dimensions, and do not ask optional 'any other "
        "roles/pages/features' questions. If the previous pending question was the business-agent "
        "suitability check, treat an affirmative answer as explicit permission to derive the proposed "
        "product-level agent requirement from the confirmed application context; treat a negative answer "
        "as a decision to keep agent_requirements empty. In both cases, do not ask the suitability question "
        "again. Omit optional details that remain unspecified.\n"
        if existing_spec
        and existing_spec.get("confirmation_status") == "pending_user_input"
        else ""
    )
    format_correction_policy = (
        "FORMAT CORRECTION: The previous response did not follow the required response protocol. "
        "Do not repeat prose, explanations, markdown, or an unstructured clarification question. "
        "If user input is required, call ask_user and return no prose. Otherwise return one complete JSON object "
        "that follows every RequirementSpec field rule below. This instruction corrects only the response format; "
        "it does not add, remove, or reinterpret any user requirement.\n"
        if format_correction
        else ""
    )
    return (
        "You are the requirements model for an app-generation workflow.\n"
        "This is a requirements-only boundary. Do not call subagents, do not delegate tasks, "
        "do not create project plans, and do not generate or modify code.\n"
        "The only tool you may call is ask_user, and only when user input is required.\n"
        f"{format_correction_policy}"
        "Analyze the user's application request and decide whether the requirement is clear enough "
        "to produce a RequirementSpec.\n"
        "A clear RequirementSpec must cover all of these aspects: 应用信息, 业务参与者, 功能模块, "
        "页面清单, 业务流程, 业务智能体需求.\n"
        "When the request explicitly says that application-level authorization is enabled, also produce an "
        "authorization_requirements object. Extract only permission controls explicitly stated in the user's "
        "business description or clarification answers into restrictedPages and restrictedOperations. Empty candidate "
        "arrays are valid and mean that the user did not request RBAC control for that business dimension. Never infer "
        "a page or operation restriction merely because the "
        "application authorization switch is enabled. The generated seed lines for authorization enablement and "
        "initial administrators are configuration facts only; they "
        "are not evidence that any business page, operation, or entity must be permission-controlled. Ask only "
        "when a permission control the user actually "
        "mentioned has ambiguous business meaning, such as which page/action it refers to or what an explicitly "
        "requested target means. Do not ask the user to invent role ids, resource keys, policy keys, database "
        "fields, SQL, or technical permission names.\n"
        "Permission clarification belongs to this early requirements conversation, before the RequirementSpec "
        "confirmation card. Ask one focused business question at a time: first the business pages/objects whose "
        "access is controlled, then the controlled business operations. Only ask "
        "a category when the user explicitly mentioned that kind of control; otherwise keep its candidate array "
        "empty. If the user mentioned a category but its meaning is incomplete, use ask_user instead of returning "
        "placeholder candidates with empty names or descriptions. A user answer of “无” means that category has "
        "no RBAC control and must remain empty.\n"
        "Authentication and RBAC resource control are separate: a login requirement does not itself create a "
        "restricted page or operation. RequirementSpec authorization candidates are business-only: "
        "restrictedPages records the business page/object name and why access is restricted; "
        "restrictedOperations records the business operation name and reason. The specialized authorization fact "
        "extraction pass assigns restrictedPages.targetPageId from this document's page catalogue; do not invent page, "
        "entity, operation, route, resourceKey, policyKey, dataRuleKey, database fields, or SQL identifiers here.\n"
        "Each permission candidate must include sourceRefs containing the relevant original business description or clarification answer and non-empty defaultGrantedRoleIds referencing user_roles[].id. If the user explicitly requests a controlled target but does not state which role receives it by default, call ask_user to select the applicable business roles; never guess or leave it empty. Do not emit unauthorizedBehavior, unauthorizedPage, unauthorizedOperation, unauthenticated, or any other configurable unauthorized-display field: page/menu and operation entries are fixed to hide for users without the matching resource, while direct page and endpoint access is rejected with 403. Data authorization is not supported in this phase: report it only through the separate authorization fact extraction capability issue, never as RequirementSpec fields.\n"
        "First identify every business participant explicitly stated in the request and put it in user_roles; this includes roles such as 管理员、审批人、运营人员、员工 when the user describes them. Do not replace an explicitly stated role with a generic business_user. RequirementSpec first records business-role facts, never runtime role-resource/member relations. Every user_roles item must have a stable lower_snake_case id, name, description, isSystemRole=false, and isInitialAdminRole=false. Do not select an initial system administrator and do not call ask_user for that selection: after business roles are recorded, the workflow presents the choice deterministically. Never decide system-administrator responsibility from a role name. The flags are metadata only and do not grant implicit permissions.\n"
        "When the configuration fact says application-level authorization is disabled but the original business description explicitly requests a permission control, return a top-level internal authorization_config_conflict object with requested=true and short evidence. Do not copy this marker into the RequirementSpec and do not silently enable authorization_requirements. Omit the marker when no business permission control was requested.\n"
        "If authorization is not enabled, return authorization_requirements.enabled=false and empty candidate "
        "arrays, and state that the application has no application-level resource authorization.\n"
        "Do not generate business entities in this stage. Entities, their fields, data sources, "
        "and database/persistence choices belong to the technical planning stage, not the requirement "
        "specification. Do not include an entities field in the returned RequirementSpec.\n"
        "Databases, persistence, API contracts, schemas, and storage choices are technical "
        "planning concerns. Do not ask the product user about them, do not expose them for confirmation, "
        "and do not include data_sources in the returned RequirementSpec.\n"
        "Business-agent requirements are user-facing AI assistants or autonomous task capabilities that the "
        "user explicitly requests or explicitly accepts after an agent suitability check. When the user has not "
        "explicitly requested, accepted, or declined a business agent, perform that agent suitability check before "
        "defaulting agent_requirements to an empty array. An application is suitable only when a concrete "
        "user-facing agent role would add material value beyond deterministic pages and fixed workflows through "
        "contextual reasoning, multi-turn natural-language guidance, cross-feature assistance, or tool-backed task "
        "execution. Ordinary CRUD, dashboards, reports, fixed approvals, search, filtering, import/export, "
        "notifications, scheduled automation, data processing, recommendation, or generated copy alone are not "
        "sufficient. If the application is suitable, call ask_user with one focused yesno question that names a "
        "concrete proposed agent role, explains the user-visible value in this application, and asks whether to "
        "integrate it. This agent suitability check is an explicit exception to the all-five-required-fields return "
        "rule, but it remains inside the same clarification-round limit and should be included in the current "
        "ask_user call alongside any other required questions. Do not add an agent_requirements item until the user "
        "accepts. If the user declines, return agent_requirements=[] and do not ask the suitability question again. "
        "If the user explicitly requested or accepted an agent, populate agent_requirements without asking a generic "
        "suitability question. If no concrete agent role would add material value, return agent_requirements=[] "
        "without asking. Always return agent_requirements as an array. "
        "Every agent_requirements item must contain exactly agentId, name, purpose, capabilities, entryPageIds, "
        "interactionMode, and boundaries. agentId must be unique lower_snake_case. capabilities must contain the "
        "explicit user-facing abilities. entryPageIds must reference pages[].pageId and may be [] only when the "
        "agent is explicitly application-wide. interactionMode describes the requested product interaction, such "
        "as conversation. boundaries records explicit read/write or business-scope limits and may be empty. This is "
        "still a product-requirements boundary: never put a model, model id, prompt, API endpoint, tool, skill, "
        "knowledge source, storage choice, implementation class, or code path in agent_requirements.\n"
        f"{clarification_policy}"
        f"{followup_policy}"
        "When asking, questions can be choice, text, or yesno. For every choice question, first decide "
        "whether the options are mutually exclusive. Set multiSelect=true for independently combinable "
        "capabilities or requirements (for example search, filtering, import/export, and pagination); "
        "do not turn their combinations into several single-choice options. Set multiSelect=false only "
        "for genuine either-or decisions. After calling ask_user, do not invent answers and do not "
        "continue planning until the user answers.\n"
        "If the requirement is clear, do not call ask_user. Return only one complete JSON object "
        "without markdown fences or commentary. The JSON must contain exactly these top-level fields: "
        "version, status, generated_at, app_info, user_roles, feature_modules, pages, business_flows, "
        "agent_requirements, authorization_requirements. Do not include any other field. "
        "app_info MUST include non-empty name and summary. Use summary as the only application-summary field; "
        "the field is named summary, not description. "
        "Do not return assumptions, product risks, or acceptance_criteria. "
        "Product acceptance criteria belong to the later ProductPlan and must not be generated in the "
        "RequirementSpec confirmation document. "
        "Every role, module, and flow must have a stable id. Normalize any non-lower_snake_case id "
        "(including Chinese characters) to an English lower_snake_case equivalent without deliberation; "
        "for example role项目经理 becomes role_project_manager. "
        "Every page must have a stable lower_snake_case pageId, name, unique path, module_id, and description. "
        "Use '/' only for the single home/dashboard page; all other pages must have business routes. "
        "When the user explicitly specifies page paths, preserve them as-is. "
        "Never return multiple pages with path '/'. "
        "Assign the home/dashboard page to the first business module listed by the user; do not create a "
        "separate dashboard module unless the user explicitly requests one. "
        "Set route_root_path to '/' when the home page path is '/'. "
        "Each business_flows item must have id, name, description, and steps. Each step is a string "
        "describing one business action; do not include role_id, page_id, or other structured fields in steps. "
        "The JSON must represent the complete current requirement, not a patch. When authorization is enabled, "
        "the complete JSON must also contain authorization_requirements with the current contract fields; do not "
        "include authorization candidate ruleId, page/entity bindings, role-resource/member assignments, "
        "resourceKey, policyKey, dataRuleKey, includes, or excludes. user_roles role ids are the only stable planning keys "
        "allowed in this RequirementSpec contract.\n\n"
        f"{revision_context}Latest user request or feedback:\n{request}"
    )


def _invoke_live_chat_model(
    request: str,
    *,
    existing_spec: dict[str, Any] | None = None,
    datasource_type: DatasourceType = "database",
    clarification_round: int = 0,
    settings: Settings | None = None,
    on_token: Callable[[str], None] | None = None,
    format_correction: bool = False,
) -> dict[str, Any]:
    """调用绑定澄清工具的需求模型，可选流式吐词。"""

    active_settings = settings or Settings.from_env()

    def _call_once() -> dict[str, Any]:
        # 每次重试必须重建 runnable 与流式迭代器：已中断的流不能续读。
        runnable = create_chat_model(active_settings).bind_tools([ask_user])
        if on_token is None:
            result = runnable.invoke(
                _requirements_prompt(
                    request,
                    existing_spec,
                    datasource_type,
                    clarification_round,
                    format_correction,
                )
            )
            return {"messages": [result]}

        accumulated_text = ""
        merged_chunk: AIMessageChunk | None = None
        complete_message: AIMessage | None = None
        for chunk in runnable.stream(
            _requirements_prompt(
                request,
                existing_spec,
                datasource_type,
                clarification_round,
                format_correction,
            )
        ):
            if isinstance(chunk, AIMessageChunk):
                # glm-5.2 流式 chunk.content 是 content block 列表（如
                # [{"text": "...", "type": "text", "index": 0}]），不是纯字符串。
                # 用 _coerce_content_text 提取 text block，否则 accumulated_text 永远为空，
                # 导致最终 agent_note 为空、spec 解析失败、应用名与页面退回固定 fallback。
                token = _coerce_content_text(chunk.content)
                if token:
                    accumulated_text += token
                    on_token(token)
                merged_chunk = chunk if merged_chunk is None else merged_chunk + chunk
            elif isinstance(chunk, AIMessage):
                # LangChain 在原生流式关闭时会让 stream() 回退为单个完整消息；
                # 必须保留该消息，否则已成功返回的正文和工具调用会被误判为空响应。
                token = _coerce_content_text(chunk.content)
                if token:
                    on_token(token)
                complete_message = chunk
        if complete_message is not None:
            return {"messages": [complete_message]}
        if merged_chunk is None:
            return {"messages": []}
        final_tool_calls = getattr(merged_chunk, "tool_calls", None) or []
        response_metadata = getattr(merged_chunk, "response_metadata", None)
        usage_metadata = getattr(merged_chunk, "usage_metadata", None)
        final = AIMessage(
            content=accumulated_text,
            tool_calls=(
                final_tool_calls if hasattr(merged_chunk, "tool_calls") else None
            ),
            id=merged_chunk.id if hasattr(merged_chunk, "id") else None,
            response_metadata=(
                response_metadata if isinstance(response_metadata, dict) else {}
            ),
            usage_metadata=(
                usage_metadata if isinstance(usage_metadata, dict) else None
            ),
        )
        return {"messages": [final]}

    return run_with_transport_retry(_call_once, operation_name="需求分析模型调用")


def analyze_requirements_with_chat_model(
    request: str,
    existing_spec: dict[str, Any] | None = None,
    *,
    datasource_type: DatasourceType = "database",
    clarification_round: int = 0,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """直接调用需求模型生成 RequirementSpec，并在关键需求不足时请求澄清。"""

    settings = Settings.from_env()

    def _analyze_once(*, format_correction: bool = False) -> dict[str, Any]:
        """执行一次需求分析，并在内容重试时启用固定协议纠正。"""

        return _analyze_requirements_once(
            request,
            existing_spec=existing_spec,
            datasource_type=datasource_type,
            clarification_round=clarification_round,
            settings=settings,
            on_token=on_token,
            format_correction=format_correction,
        )

    try:
        return _analyze_once()
    except ValueError as exc:
        # 模型输出的 JSON 结构损坏（引号未转义等）属于内容抖动：
        # 恢复层修不好时重新调用一次，新输出大概率自愈，避免直接打断 workflow。
        if not _is_malformed_spec_json_error(exc):
            raise
        logger.warning(
            "requirement_spec_content_retry: 模型返回的需求 JSON 不完整，重试一次：%s",
            exc,
        )
        return _analyze_once(format_correction=True)


def _is_malformed_spec_json_error(exc: ValueError) -> bool:
    """是否为"模型返回的需求 JSON 损坏/不完整"类错误（可安全重试模型调用）。"""

    message = str(exc)
    return message.startswith("需求 AI 未返回完整") or message.startswith(
        "需求 AI 返回的新 RequirementSpec 缺少完整字段"
    )


def _log_requirement_model_response_diagnostics(
    message: Any,
    agent_note: str,
    agent_spec: Any,
    *,
    configured_max_tokens: int | None,
) -> None:
    """记录需求模型响应的脱敏结构摘要，不输出用户需求或模型正文。"""

    response_metadata = getattr(message, "response_metadata", None)
    response_metadata = response_metadata if isinstance(response_metadata, dict) else {}
    usage_metadata = getattr(message, "usage_metadata", None)
    token_usage = response_metadata.get("token_usage")
    usage = (
        usage_metadata
        if isinstance(usage_metadata, dict)
        else token_usage
        if isinstance(token_usage, dict)
        else {}
    )
    output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
    finish_reason = response_metadata.get("finish_reason") or response_metadata.get(
        "finishReason"
    )
    logger.info(
        "requirement_model_response response_chars=%s response_sha256=%s parsed_keys=%s "
        "tool_call_count=%s finish_reason=%s output_tokens=%s configured_max_tokens=%s",
        len(agent_note),
        sha256(agent_note.encode("utf-8")).hexdigest()[:16],
        sorted(str(key) for key in agent_spec) if isinstance(agent_spec, dict) else [],
        len(getattr(message, "tool_calls", None) or []),
        finish_reason,
        output_tokens if isinstance(output_tokens, int) else None,
        configured_max_tokens,
    )


def _analyze_requirements_once(
    request: str,
    *,
    existing_spec: dict[str, Any] | None,
    datasource_type: DatasourceType,
    clarification_round: int,
    settings: Settings,
    on_token: Callable[[str], None] | None,
    format_correction: bool = False,
) -> dict[str, Any]:
    """单次需求分析：调用模型、解析 RequirementSpec、合并澄清上下文。"""

    agent_result = _invoke_live_chat_model(
        request,
        existing_spec=existing_spec,
        datasource_type=datasource_type,
        clarification_round=clarification_round,
        settings=settings,
        on_token=on_token,
        format_correction=format_correction,
    )
    messages = agent_result.get("messages", [])
    message = messages[-1] if messages else None
    content = getattr(message, "content", "") if message is not None else ""
    agent_note = _coerce_content_text(content) or ""
    analysis_source = "direct_chat_model"

    agent_spec = extract_json_object(agent_note)
    asks_for_clarification = any(
        getattr(message, "tool_calls", None) for message in messages
    )
    if not asks_for_clarification:
        agent_spec = _recover_requirement_spec_json(agent_note, agent_spec)
    _log_requirement_model_response_diagnostics(
        message,
        agent_note,
        agent_spec,
        configured_max_tokens=getattr(settings, "default_max_tokens", None),
    )
    if not asks_for_clarification and not isinstance(agent_spec, dict):
        # 需求已被判定为清晰时必须有完整 JSON，不能退回固定页面模板继续向下游传播。
        raise ValueError("需求 AI 未返回完整 RequirementSpec JSON。")
    # ask_user 不采用并行 JSON；权限角色和规则由独立事实提取步骤写入草稿。
    authorization_config_conflict = _authorization_config_conflict_from_agent_spec(
        request,
        agent_spec if not asks_for_clarification else None,
    )
    effective_agent_spec = (
        deepcopy(agent_spec)
        if not asks_for_clarification and isinstance(agent_spec, dict)
        else None
    )
    if isinstance(effective_agent_spec, dict):
        # 此标记只驱动配置前置澄清，不能成为正式需求文档的一部分。
        effective_agent_spec.pop("authorization_config_conflict", None)
        effective_agent_spec = _normalize_optional_agent_requirement_fields(
            effective_agent_spec
        )
    if not asks_for_clarification:
        _validate_complete_requirement_spec(
            effective_agent_spec,
            existing_spec=existing_spec,
        )
    spec = create_requirement_spec(
        request,
        agent_note=agent_note,
        agent_spec=effective_agent_spec,
        existing_spec=existing_spec,
        authoritative_agent_spec=(
            isinstance(effective_agent_spec, dict) and isinstance(existing_spec, dict)
        ),
        datasource_type=datasource_type,
        # 实时模型的 RequirementSpec 必须完全来自模型或用户明确回答，禁止启用固定页面兜底。
        allow_inferred_defaults=False,
    )
    # 角色事实独立于是否开启权限：后续“谁是初始系统管理员”的选择只能基于这里识别的业务角色。
    authorization_facts = _extract_authorization_facts(
        request,
        existing_spec,
        settings,
        spec.get("pages") if isinstance(spec.get("pages"), list) else [],
    )
    effective_agent_spec = _merge_authorization_facts(
        effective_agent_spec,
        authorization_facts,
        existing_spec,
    )
    spec = create_requirement_spec(
        request,
        agent_note=agent_note,
        agent_spec=effective_agent_spec,
        existing_spec=existing_spec,
        authoritative_agent_spec=True,
        datasource_type=datasource_type,
        allow_inferred_defaults=False,
    )
    clarification = extract_ask_user_clarification(agent_result, spec)
    spec["clarification_questions"] = clarification["questions"]
    spec["clarification_status"] = clarification["status"]
    spec["unresolved_requirement_dimensions"] = clarification.get(
        "all_unresolved_dimensions", []
    )
    spec["analyzed_by"] = {
        "agent": "chat-model",
        "mode": "direct",
        "model": settings.model_name,
        "source": analysis_source,
    }
    spec["analysis_source"] = analysis_source
    spec["agent_spec_used"] = isinstance(agent_spec, dict)
    clarification["requested_by"] = "chat-model"
    clarification["analysis_source"] = analysis_source
    clarification["analysis_note"] = json.dumps(
        {
            "mode": "direct",
            "source": analysis_source,
            "agent_note": agent_note,
        },
        ensure_ascii=False,
    )
    return {
        "requirement_spec": spec,
        "clarification": clarification,
        "authorization_config_conflict": authorization_config_conflict,
    }


def _authorization_config_conflict_from_agent_spec(
    request: str,
    agent_spec: Any,
) -> dict[str, Any] | None:
    """识别模型发现的业务权限要求与关闭配置之间的冲突，不使用关键词猜测业务。"""

    if _authorization_enabled_from_request(request) is not False or not isinstance(
        agent_spec, dict
    ):
        return None
    conflict = agent_spec.get("authorization_config_conflict")
    if not isinstance(conflict, dict) or conflict.get("requested") is not True:
        return None
    evidence = conflict.get("evidence")
    evidence_items = (
        [str(item).strip() for item in evidence if str(item).strip()]
        if isinstance(evidence, list)
        else []
    )
    # 配置冲突必须有模型给出的原始需求证据，避免仅凭“管理员”等角色名称产生误报。
    if not evidence_items:
        return None
    return {
        "requested": True,
        "evidence": evidence_items[:8],
    }


_REQUIREMENT_SPEC_CONTRACT_MARKERS = ('"app_info"', '"user_roles"', '"feature_modules"')


def _requirement_spec_fields_missing(agent_spec: Any) -> bool:
    """提取结果是否缺 RequirementSpec 顶层字段（嵌套子对象被误当整体的特征）。"""

    if not isinstance(agent_spec, dict):
        return True
    return any(
        field not in agent_spec
        for field in ("app_info", "user_roles", "feature_modules", "pages")
    )


def _recover_requirement_spec_json(agent_note: str, agent_spec: Any) -> Any:
    """模型 JSON 引号损坏时的受控恢复：修复后重解析，仅在能拿到完整顶层字段时采用。

    与 build_result_coordinator 的恢复模式一致：只在文本里存在契约标记时尝试，
    避免把无关文本误修复成看似合法的内容。
    """

    if not _requirement_spec_fields_missing(agent_spec):
        return agent_spec
    if not any(marker in agent_note for marker in _REQUIREMENT_SPEC_CONTRACT_MARKERS):
        return agent_spec
    repaired_note = repair_unescaped_json_string_quotes(agent_note)
    if repaired_note == agent_note:
        return agent_spec
    repaired_spec = extract_json_object(repaired_note)
    if _requirement_spec_fields_missing(repaired_spec):
        return agent_spec
    logger.warning(
        "requirement_spec_json_recovered: 模型输出经引号修复后解析出完整 RequirementSpec"
    )
    return repaired_spec


def _validate_complete_requirement_spec(
    agent_spec: Any,
    *,
    existing_spec: dict[str, Any] | None = None,
) -> None:
    """拒绝不完整模型结果，避免空业务结构进入需求确认。"""

    if not isinstance(agent_spec, dict):
        raise ValueError("需求 AI 未返回完整的新 RequirementSpec。")
    app_info = agent_spec.get("app_info")
    missing_fields = [
        field_name
        for field_name in (
            "user_roles",
            "feature_modules",
            "pages",
            "business_flows",
            "agent_requirements",
        )
        if not isinstance(agent_spec.get(field_name), list)
    ]
    if not isinstance(app_info, dict):
        missing_fields.insert(0, "app_info")
    else:
        # 模型有时用 description 代替 summary，兼容回退
        if not str(app_info.get("summary") or "").strip() and str(
            app_info.get("description") or ""
        ).strip():
            app_info["summary"] = app_info["description"]
        if (
            not str(app_info.get("name") or "").strip()
            or not str(app_info.get("summary") or "").strip()
        ):
            missing_fields.insert(0, "app_info.name/summary")
    for field_name in ("feature_modules", "pages"):
        value = agent_spec.get(field_name)
        if isinstance(value, list) and not value:
            missing_fields.append(f"{field_name}(non-empty)")
    authorization = agent_spec.get("authorization_requirements")
    existing_authorization = (
        existing_spec.get("authorization_requirements")
        if isinstance(existing_spec, dict)
        and isinstance(existing_spec.get("authorization_requirements"), dict)
        else {}
    )
    authorization_required = (
        "authorization_requirements" in agent_spec
        or existing_authorization.get("enabled") is True
    )
    if authorization_required and not isinstance(authorization, dict):
        missing_fields.append("authorization_requirements")
    elif isinstance(authorization, dict) and authorization.get("enabled") is True:
        for field_name in ("restrictedPages", "restrictedOperations"):
            if not isinstance(authorization.get(field_name), list):
                missing_fields.append(f"authorization_requirements.{field_name}")
    agent_requirements = agent_spec.get("agent_requirements")
    page_ids = {
        str(page.get("pageId") or "").strip()
        for page in agent_spec.get("pages", [])
        if isinstance(page, dict) and str(page.get("pageId") or "").strip()
    }
    agent_ids: set[str] = set()
    expected_agent_fields = {
        "agentId",
        "name",
        "purpose",
        "capabilities",
        "entryPageIds",
        "interactionMode",
        "boundaries",
    }
    if isinstance(agent_requirements, list):
        for index, agent in enumerate(agent_requirements):
            field_prefix = f"agent_requirements[{index}]"
            if not isinstance(agent, dict) or set(agent) != expected_agent_fields:
                missing_fields.append(f"{field_prefix}(complete-fields)")
                continue
            agent_id = str(agent.get("agentId") or "").strip()
            if not _LOWER_SNAKE_CASE_PATTERN.fullmatch(agent_id):
                missing_fields.append(f"{field_prefix}.agentId(lower_snake_case)")
            elif agent_id in agent_ids:
                missing_fields.append(f"{field_prefix}.agentId(unique)")
            else:
                agent_ids.add(agent_id)
            for field_name in ("name", "purpose", "interactionMode"):
                if not str(agent.get(field_name) or "").strip():
                    missing_fields.append(f"{field_prefix}.{field_name}")
            capabilities = agent.get("capabilities")
            if not isinstance(capabilities, list) or not _string_list(capabilities):
                missing_fields.append(f"{field_prefix}.capabilities(non-empty)")
            entry_page_ids = agent.get("entryPageIds")
            if not isinstance(entry_page_ids, list):
                missing_fields.append(f"{field_prefix}.entryPageIds")
            elif set(_string_list(entry_page_ids)) - page_ids:
                missing_fields.append(f"{field_prefix}.entryPageIds(page-reference)")
            if not isinstance(agent.get("boundaries"), list):
                missing_fields.append(f"{field_prefix}.boundaries")
    if missing_fields:
        raise ValueError(
            "需求 AI 返回的新 RequirementSpec 缺少完整字段："
            + "、".join(missing_fields)
        )


def _normalize_optional_agent_requirement_fields(
    agent_spec: dict[str, Any],
) -> dict[str, Any]:
    """规范化可无损解释的智能体边界，同时保留其他字段的严格校验。"""

    normalized = deepcopy(agent_spec)
    agent_requirements = normalized.get("agent_requirements")
    if not isinstance(agent_requirements, list):
        return normalized
    for agent in agent_requirements:
        if not isinstance(agent, dict):
            continue
        boundaries = agent.get("boundaries")
        if boundaries is None:
            # boundaries 的空数组有明确契约语义，不需要模型或产品侧发明业务事实。
            agent["boundaries"] = []
        elif isinstance(boundaries, str):
            # 单个字符串仍是明确的一条业务边界，无损收敛到正式数组契约。
            normalized_boundary = boundaries.strip()
            agent["boundaries"] = (
                [normalized_boundary] if normalized_boundary else []
            )
    return normalized
