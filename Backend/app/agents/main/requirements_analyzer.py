from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from typing import Any, Callable

from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.model_operations import model_operation, observe_model_operation
from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.data_source_policy import DatasourceType
from app.services.model_transport_retry import run_with_transport_retry
from app.services.requirement_spec import (
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
    existing_authorization = (
        existing_spec.get("authorization_requirements")
        if isinstance(existing_spec, dict)
        and isinstance(existing_spec.get("authorization_requirements"), dict)
        else {}
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
        "referencing user_roles ids and must not be empty. Return [] when the request does not explicitly state the "
        "default role grant; never guess a role. A globally unavailable feature is not authorization: statements such "
        "as 'read-only', 'view only', or 'cannot add, delete, or edit' define the application's feature scope for "
        "everyone and must never become restrictedPages or restrictedOperations. Only emit a restriction when the "
        "request explicitly assigns access to one or more roles, members, or other authorization subjects. "
        "A role catalogue, a workflow actor, and an authentication fact establish participants, normal work, or identity "
        "only; none is authorization evidence. Do not combine separate statements into an access policy. Emit a restriction "
        "only when one self-contained requirement fact explicitly states a restriction, denial, exclusivity, grant, or "
        "capability difference between authenticated subjects. "
        "dataAuthorizationIssues contains only explicit data-authorization requests that V1 cannot "
        "implement. Each item must contain exactly description and sourceRefs. Add an issue when different members, roles, "
        "organizations, projects, customers, or other relations determine which records can be read, modified, or created. "
        "Do not add an issue for a fixed business query such as 'my applications' unless it is an authorization boundary. "
        "Never emit empty objects, placeholders, allowedRoles, resource keys, technical ids, routes, API fields, SQL, "
        "or initial-system-administrator fields.\n"
        "Do not ask the user to repeat facts already stated. If a fact is explicit, express it completely using the "
        "required fields. For revisions, apply the latest request to the existing authorization controls and preserve "
        "unrelated controls and grants. When changing only default role grants, copy the existing control name exactly; "
        "do not add page-name prefixes, rephrase it, or create another control for the same operation. Change or remove "
        "a control name only when the user explicitly requests that change. Preserve existing role ids.\n"
        f"Existing business roles, if any:\n{json.dumps(existing_roles, ensure_ascii=False)}\n\n"
        f"Existing authorization controls, if any:\n{json.dumps(existing_authorization, ensure_ascii=False)}\n\n"
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


def _is_global_feature_availability_source(value: object) -> bool:
    """判断来源文本是否只是在声明全局功能不可用，而非按角色授权。"""

    text = str(value or "").strip().lower()
    if not text:
        return False
    has_global_restriction = bool(
        re.search(
            r"(?:只读|仅查看|只能查看|不可|不能|不可以|禁止|不支持).{0,12}"
            r"(?:新增|添加|创建|删除|修改|编辑|更新)",
            text,
        )
    )
    has_authorization_subject = bool(
        re.search(r"(?:仅|只有|角色|权限|授权|管理员|hr|审批人|运营人员)", text)
    )
    return has_global_restriction and not has_authorization_subject


def _remove_global_feature_availability_controls(value: Any) -> Any:
    """移除被模型误写成权限规则的全局只读功能声明。"""

    if not isinstance(value, dict):
        return value
    sanitized = deepcopy(value)
    authorization = sanitized.get("authorization_requirements")
    if not isinstance(authorization, dict):
        return sanitized
    for field_name in ("restrictedPages", "restrictedOperations"):
        items = authorization.get(field_name)
        if not isinstance(items, list):
            continue
        # 只有所有来源都明确是全局功能限制时才删除，避免覆盖真正的角色权限规则。
        authorization[field_name] = [
            item
            for item in items
            if not (
                isinstance(item, dict)
                and (sources := _string_list(item.get("sourceRefs")))
                and all(_is_global_feature_availability_source(source) for source in sources)
            )
        ]
    return sanitized


def _authorization_evidence_prompt(request: str) -> str:
    """构造窄职责授权证据判定提示，不允许其生成权限规则。"""

    return (
        "Classify whether this application requirement explicitly describes authorization, not authentication or business flows. "
        "Return exactly one JSON object: {\"hasAuthorizationRequirement\": boolean, \"evidence\": string[]}.\n"
        "Authorization requires an explicit restriction, denial, exclusivity, grant, or capability difference between "
        "already authenticated principals. Authentication answers who is logged in; a business actor answers who performs "
        "a normal workflow. Neither is authorization evidence. Do not combine separate role and workflow statements.\n"
        "Return false with [] for authentication requirements, participant catalogues, and ordinary workflow statements. "
        "Return true only for a self-contained statement of restriction, denial, exclusivity, grant, or capability difference "
        "between authenticated subjects.\n"
        "Each evidence value must be a verbatim, non-empty substring of the original requirement that independently proves "
        "the explicit authorization assertion.\n\n"
        f"Original requirement:\n{request}"
    )


def _authorization_evidence(value: Any, request: str) -> dict[str, Any]:
    """校验窄分类器结论，只有可追溯的原文证据才能开启权限抽取。"""

    if not isinstance(value, dict) or set(value) != {"hasAuthorizationRequirement", "evidence"}:
        return {"hasAuthorizationRequirement": False, "evidence": []}
    request_text = str(request or "")
    # 逐条核对原文引用；混入一条改写不能否定其余合法授权证据。
    evidence = [item for item in _string_list(value.get("evidence")) if item in request_text]
    has_requirement = value.get("hasAuthorizationRequirement") is True
    if not has_requirement or not evidence:
        return {"hasAuthorizationRequirement": False, "evidence": []}
    return {"hasAuthorizationRequirement": True, "evidence": evidence}


@observe_model_operation("classify_authorization_requirements", business_stage="requirements")
def _classify_authorization_evidence(request: str, settings: Settings) -> dict[str, Any]:
    """以独立窄模型判定是否存在授权证据，阻断角色职责误入权限抽取。"""

    result = create_chat_model(settings).invoke(_authorization_evidence_prompt(request))
    payload = extract_json_object(_coerce_content_text(getattr(result, "content", "")) or "")
    return _authorization_evidence(payload, request)


def _remove_unauthorized_authorization_candidates(
    value: Any,
    evidence: dict[str, Any],
) -> Any:
    """在证据门关闭时清除所有模型候选，保持业务角色和认证事实独立。"""

    if not isinstance(value, dict) or evidence.get("hasAuthorizationRequirement") is True:
        return value
    sanitized = deepcopy(value)
    authorization = sanitized.get("authorization_requirements")
    if not isinstance(authorization, dict):
        return sanitized
    for field_name in ("restrictedPages", "restrictedOperations"):
        if isinstance(authorization.get(field_name), list):
            authorization[field_name] = []
    # 非显式授权证据不得留下任何可被误解为权限启用的业务规则。
    authorization.pop("enabled", None)
    authorization.pop("initialAdminRoleId", None)
    return sanitized


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
            if not grants:
                errors.append(
                    f"权限事实模型输出.{field_name}[{index}] 缺少默认角色授权。"
                )
            elif any(role_id not in role_ids for role_id in grants):
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
        with model_operation(
            "repair_authorization_facts" if _attempt else "extract_authorization_facts",
            business_stage="requirements",
        ):
            result = create_chat_model(settings).invoke(prompt)
        payload = extract_json_object(
            _coerce_content_text(getattr(result, "content", "")) or ""
        )
        # 全局只读声明不应因模型误判而触发应用权限初始化。
        payload = _remove_global_feature_availability_controls(payload)
        errors = _validate_authorization_fact_output(payload, pages)
        if not errors:
            return payload
        feedback = "\n".join(f"- {error}" for error in errors[:12])
    raise ValueError("权限业务事实自动修复达到上限后仍未通过校验：" + feedback)


def _authorization_facts_for_requirement(
    request: str,
    existing_spec: dict[str, Any] | None,
    settings: Settings,
    pages: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """先执行授权证据门，再按需提取角色-资源事实。"""

    evidence = _classify_authorization_evidence(request, settings)
    if evidence["hasAuthorizationRequirement"]:
        return _extract_authorization_facts(request, existing_spec, settings, pages), evidence
    return (
        {
            "user_roles": [],
            "authorization_requirements": {
                "restrictedPages": [],
                "restrictedOperations": [],
                "dataAuthorizationIssues": [],
            },
        },
        evidence,
    )


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
            existing_items = authorization.get(field_name)
            # 独立提取出的非空候选优先，因为它经过专门的契约校验。反过来，空
            # 数组并不能证明主需求模型已给出的完整候选是错误的：实际模型曾将
            # “HR 用于查看人员列表”误判为角色职责而非访问限制，进而把已确认的
            # 页面授权和权限开关一并清空。仅在两边都没有候选时才保留空数组。
            if fact_items or not isinstance(existing_items, list) or not existing_items:
                authorization[field_name] = fact_items
    # 已通过独立契约校验的受控页面或操作，是用户要求应用权限能力的确定性证据。
    # 不依赖“涉及权限控制：是”这类表单式措辞，避免自然语言需求被旧开关静默清空。
    if any(
        isinstance(authorization.get(field_name), list)
        and bool(authorization[field_name])
        for field_name in ("restrictedPages", "restrictedOperations")
    ):
        authorization.pop("enabled", None)
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
        "Treat the latest feedback as an incremental patch to the existing RequirementSpec. "
        "Apply explicit changes and preserve unaffected business facts and stable identifiers. Add newly "
        "requested pages to the existing list. Remove a page only when the user asks to remove it or to replace "
        "or redesign the whole application. New feedback takes precedence only where it directly conflicts "
        "with the baseline.\n"
        "Honor names and paths the user explicitly specifies. Apply naming rules to new items and requested "
        "renames. Update affected references when an identifier changes. Summarize the complete merged "
        "application in app_info.summary. Return the complete document to authoritatively replace the old document; "
        "never return a partial patch.\n"
        f"Existing RequirementSpec:\n{json.dumps(visible_existing_spec, ensure_ascii=False)}\n"
        if visible_existing_spec
        else "Create a new RequirementSpec from the user request.\n"
    )
    round_context = (
        f"The user has answered all {MAX_REQUIREMENT_CLARIFICATION_ROUNDS} clarification rounds. "
        "Merge the answers into the best complete JSON supported by the available facts; "
        "never call ask_user in this pass.\n"
        if bounded_round >= MAX_REQUIREMENT_CLARIFICATION_ROUNDS
        else (
            f"This is clarification round {next_round} of {MAX_REQUIREMENT_CLARIFICATION_ROUNDS} "
            f"({bounded_round} completed). In the last round, include all remaining required questions "
            "allowed by the clarification policy.\n"
        )
    )
    followup_context = (
        "The latest input answers a previous clarification. Merge the answers as requirement facts, "
        "and do not ask the same questions again. Clarification answers do not confirm the artifact.\n"
        if existing_spec
        and existing_spec.get("confirmation_status") == "pending_user_input"
        else ""
    )

    return (
        "You are the requirements model for an app-generation workflow. Produce a complete RequirementSpec "
        "from the user's request, clarification answers, and any existing RequirementSpec provided below. "
        "ask_user is the only tool available. Use it under the clarification rules. "
        "Do not delegate, plan implementation, or write code.\n"
        "\n1. Capture business facts in the existing fields\n"
        "Resolve shorthand and ambiguities from the supplied context first. Preserve explicitly requested "
        "information, operations, states, relationships, constraints, page structure, and interactions. "
        "Leave optional details unspecified unless supported by the requirements. Do not invent defaults, "
        "uniqueness constraints, states, permissions, or features.\n"
        "- app_info: Record the application name and summarize its goals and scope.\n"
        "- user_roles: Record every explicitly stated business participant and their responsibilities. "
        "Keep named roles instead of replacing them with a generic business_user.\n"
        "- feature_modules: Group by business capability. Use description to capture the module's purpose, "
        "the information it manages, supported operations, relevant relationships, and business rules. "
        "Put cross-page rules in the relevant module description.\n"
        "- pages: Include each required page and link it to its module through module_id. Use description "
        "to explain the page's purpose, what it displays or maintains, what users can do, and any "
        "required interaction. Preserve explicit choices such as editing within a table.\n"
        "- business_flows: Describe processes supported by the requirements. Use description for the "
        "purpose and steps for ordered business actions and outcomes, including any stated prerequisites "
        "or constraints. Explain how module rules apply in a page or flow without repeating them in full.\n"
        "- authentication_requirements and authorization_requirements: Follow the access rules in section 3.\n"
        "Leave detailed product behavior design and acceptance criteria to ProductPlan. Technical planning "
        "owns entity definitions, data sources, persistence, API contracts, and code design. "
        "Do not ask about or include those decisions in this artifact.\n"
        "\n2. Name business concepts by their scope\n"
        "For each new concept, first determine its full business name and English identifier stem from its "
        "purpose, applicable objects, and rules. Keep qualifiers that distinguish its scope, even if no other "
        "concept has the same short name. A section heading or provisional page title can be shorthand. Treat "
        "it as a fixed label only if the user explicitly asks to keep that wording.\n"
        "Use the full concept in new module, page, and flow names. Derive their new IDs from the same "
        "lower_snake_case stem and retain that stem's scope words in the new route segment in kebab-case. "
        "Add a purpose suffix (such as _management or _list) where it helps distinguish items. A qualifier "
        "appearing only in a description does not satisfy this rule. Short labels may be used inside a page "
        "whose scope is already clear. Preserve explicitly specified paths and unaffected existing identifiers.\n"
        "A reference or dedicated management page alone does not establish ownership. Use one name for the "
        "same concept, distinguish different concepts that share a label, and name explicitly shared concepts "
        "for their common purpose. A dedicated page for a scoped concept retains that concept's scope.\n"
        "- Example 1: Apparel and brands. Apparel uses a brand list maintained specifically for apparel. "
        "Use the 'Apparel Brand' concept with the stem apparel_brand, even if the request calls its dedicated "
        "page 'Brand List'. Use the equivalent of 'Apparel Brand Management' and 'Apparel Brand List' in "
        "display names, with module ID apparel_brand_management, pageId apparel_brand_list, and path "
        "/apparel-brand. The page's module_id references apparel_brand_management. A label inside an apparel "
        "form may still be 'Brand'.\n"
        "- Example 2: Equipment maintenance records and employee training records. If the request calls both "
        "'records', retain the full names, with identifiers such as equipment_maintenance_record and "
        "employee_training_record. Retain the relevant scope even when only one kind is requested.\n"
        "- Example 3: Orders and users. An order references the user who placed it. The independent concept "
        "keeps the name 'User'. The reference does not make it an 'Order User'.\n"
        "Derive actual names from the current requirements. Before returning JSON, compare each newly named "
        "item with its description. If the description establishes a scoped concept but its name, identifier, "
        "or generated route drops that scope, fix those values and their references. Apply this check when "
        "producing the first complete draft, including after clarification.\n"
        "\n3. Capture authentication and authorization separately\n"
        "- Authentication: Cite the explicit request to add or enable login in authentication_requirements.sourceRefs. "
        "Use an empty sourceRefs array when there is no such request. Login alone does not restrict a page or operation.\n"
        "- Authorization: Record a candidate only when the request or a clarification answer explicitly states "
        "a restriction, denial, exclusive access, grant, or difference in what authenticated users may do. "
        "Roles, workflow participants, ordinary resource use, login, and platform-generated configuration lines "
        "are insufficient evidence. A capability enabled in platform configuration does not imply a business restriction.\n"
        "- Use restrictedPages for controlled pages or business objects and restrictedOperations for controlled "
        "operations. Each candidate needs a name, description, rationale, sourceRefs citing the original "
        "requirement or clarification, and non-empty defaultGrantedRoleIds referencing user_roles IDs. "
        "The separate authorization extraction pass binds restricted pages to targetPageId. "
        "Use an empty array for a category with no explicit control.\n"
        "- For an explicitly requested control with an unclear target or missing default roles, use the "
        "clarification policy instead of guessing or emitting placeholders. An answer meaning 'none' "
        "leaves that category empty. The separate extraction pass reports unsupported data authorization as "
        "a capability issue. Do not represent it as a new RequirementSpec field.\n"
        "- The platform validates these facts, controls application-level capability switches, creates "
        "SYSTEM_ADMIN, and separately collects a real initial-administrator subjectId when required. "
        "Do not assign that administrator, emit configuration conflicts or extra enablement fields, "
        "or specify runtime role, member, or resource assignments. The platform hides restricted entries "
        "from users without access and rejects direct access with HTTP 403. Omit configurable unauthorized "
        "display fields such as unauthorizedBehavior, unauthorizedPage, unauthorizedOperation, and unauthenticated.\n"
        "\n4. Clarify only when required\n"
        f"This workflow allows at most {MAX_REQUIREMENT_CLARIFICATION_ROUNDS} clarification rounds.\n"
        f"{round_context}"
        f"{followup_context}"
        "Check these required fields in order: app_info.name, user_roles, feature_modules, pages, and "
        "business_flows. The name must be non-empty and each list must contain at least one entry. "
        "Use facts that are stated or can be directly inferred. When clarification is still available and "
        "any of these facts are missing, call ask_user once for exactly those missing fields. "
        "Check the required fields directly and do not count gaps or classify them by importance.\n"
        "Explicit access controls are a separate reason to clarify. Ask one permission category per turn, "
        "clarifying controlled pages or business objects before controlled operations. When the user has "
        "explicitly requested data access restrictions, clarify only their business meaning as needed. "
        "Ask only about a category the user raised.\n"
        "Use choice, text, or yesno questions. For choice questions, use multiSelect=true for independently "
        "combinable options and multiSelect=false for mutually exclusive options. After calling ask_user, "
        "wait for the answers before producing a RequirementSpec.\n"
        "Do not ask for technical identifiers, page routes, database details, generic confirmation, or "
        "optional features. Derive required identifiers and routes from the business meaning. "
        f"After the user has answered round {MAX_REQUIREMENT_CLARIFICATION_ROUNDS}, never call ask_user again.\n"
        "\n5. Apply revisions to the existing requirements\n"
        f"{revision_context}"
        "\n6. Return the current JSON contract\n"
        "When no clarification is needed or the round limit has been reached, return exactly one complete "
        "JSON object without Markdown or commentary. The only top-level fields are version, status, generated_at, "
        "app_info, user_roles, feature_modules, pages, business_flows, authentication_requirements, "
        "and authorization_requirements.\n"
        "- app_info MUST include non-empty name and summary. Use summary as the only application-summary field.\n"
        "- Use stable English lower_snake_case values for role, module, and flow IDs and for pageId. "
        "Each user_roles entry contains only id, name, and description.\n"
        "- Each page has pageId, a Chinese display name, a unique path, module_id, and description. "
        "Do not use the reserved pageId values welcome and authorization_management for business pages. "
        "Preserve explicitly specified paths. Use '/' only for the single home or dashboard page. "
        "Assign that page to the first business module the user lists unless they explicitly request a separate "
        "dashboard module. Set app_info.route_root_path to '/' when the home page path is '/'.\n"
        "- Each business_flows entry contains id, name, description, and steps. Each step is a string "
        "describing one business action.\n"
        "- authentication_requirements contains exactly enabled and sourceRefs. In authorization_requirements, "
        "emit only the current candidate fields. Omit ruleId, technical bindings, resourceKey, policyKey, "
        "dataRuleKey, includes, and excludes. Use role IDs only where the candidate contract requires them.\n"
        "- Do not return assumptions, product risks, or acceptance_criteria. Omit entities and do not include "
        "data_sources or other technical planning fields.\n"
        f"\nLatest user request or feedback:\n{request}"
    )


def _invoke_live_chat_model(
    request: str,
    *,
    existing_spec: dict[str, Any] | None = None,
    datasource_type: DatasourceType = "database",
    clarification_round: int = 0,
    settings: Settings | None = None,
    on_token: Callable[[str], None] | None = None,
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
                )
            )
            return {"messages": [result]}

        accumulated_text = ""
        merged_chunk: AIMessageChunk | None = None
        for chunk in runnable.stream(
            _requirements_prompt(
                request,
                existing_spec,
                datasource_type,
                clarification_round,
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
        if merged_chunk is None:
            return {"messages": []}
        final_tool_calls = getattr(merged_chunk, "tool_calls", None) or []
        final = AIMessage(
            content=accumulated_text,
            tool_calls=(
                final_tool_calls if hasattr(merged_chunk, "tool_calls") else None
            ),
            id=merged_chunk.id if hasattr(merged_chunk, "id") else None,
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
    business_operation: str = "analyze_requirements",
) -> dict[str, Any]:
    """直接调用需求模型生成 RequirementSpec，并在关键需求不足时请求澄清。"""

    settings = Settings.from_env()

    def _analyze_once() -> dict[str, Any]:
        return _analyze_requirements_once(
            request,
            existing_spec=existing_spec,
            datasource_type=datasource_type,
            clarification_round=clarification_round,
            settings=settings,
            on_token=on_token,
        )

    try:
        with model_operation(business_operation, business_stage="requirements"):
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
        with model_operation("repair_requirement_spec", business_stage="requirements"):
            return _analyze_once()


def _is_malformed_spec_json_error(exc: ValueError) -> bool:
    """是否为"模型返回的需求 JSON 损坏/不完整"类错误（可安全重试模型调用）。"""

    message = str(exc)
    return message.startswith("需求 AI 未返回完整") or message.startswith(
        "需求 AI 返回的新 RequirementSpec 缺少完整字段"
    )


def _analyze_requirements_once(
    request: str,
    *,
    existing_spec: dict[str, Any] | None,
    datasource_type: DatasourceType,
    clarification_round: int,
    settings: Settings,
    on_token: Callable[[str], None] | None,
) -> dict[str, Any]:
    """单次需求分析：调用模型、解析 RequirementSpec、合并澄清上下文。"""

    agent_result = _invoke_live_chat_model(
        request,
        existing_spec=existing_spec,
        datasource_type=datasource_type,
        clarification_round=clarification_round,
        settings=settings,
        on_token=on_token,
    )
    messages = agent_result.get("messages", [])
    content = getattr(messages[-1], "content", "") if messages else ""
    agent_note = _coerce_content_text(content) or ""
    analysis_source = "direct_chat_model"

    agent_spec = extract_json_object(agent_note)
    asks_for_clarification = any(
        getattr(message, "tool_calls", None) for message in messages
    )
    if not asks_for_clarification:
        agent_spec = _recover_requirement_spec_json(agent_note, agent_spec)
    if not asks_for_clarification and not isinstance(agent_spec, dict):
        # 需求已被判定为清晰时必须有完整 JSON，不能退回固定页面模板继续向下游传播。
        raise ValueError("需求 AI 未返回完整 RequirementSpec JSON。")
    # ask_user 不采用并行 JSON；权限角色和规则由独立事实提取步骤写入草稿。
    effective_agent_spec = (
        deepcopy(agent_spec)
        if not asks_for_clarification and isinstance(agent_spec, dict)
        else None
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
    # 先判定“是否存在已认证主体间的显式能力差异”，再运行专门的权限事实提取。
    # 角色清单、登录和业务流程不具备这类证据，不能被专门抽取器二次放大为 RBAC。
    authorization_facts, authorization_evidence = _authorization_facts_for_requirement(
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
    # 主需求模型的候选同样必须经过同一证据门，禁止在独立抽取未运行时复活。
    effective_agent_spec = _remove_unauthorized_authorization_candidates(
        effective_agent_spec,
        authorization_evidence,
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
    authorization_required = "authorization_requirements" in agent_spec or any(
        bool(existing_authorization.get(field_name))
        for field_name in ("restrictedPages", "restrictedOperations")
    )
    if authorization_required and not isinstance(authorization, dict):
        missing_fields.append("authorization_requirements")
    elif isinstance(authorization, dict):
        for field_name in ("restrictedPages", "restrictedOperations"):
            if not isinstance(authorization.get(field_name), list):
                missing_fields.append(f"authorization_requirements.{field_name}")
    if missing_fields:
        raise ValueError(
            "需求 AI 返回的新 RequirementSpec 缺少完整字段："
            + "、".join(missing_fields)
        )
