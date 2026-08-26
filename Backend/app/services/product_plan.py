from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Any

from app.services.requirement_spec import product_acceptance_criteria

PRODUCT_PLAN_SCHEMA_VERSION = "product-plan.v5"
_STATE_REQUIREMENT_KEYS = ("loading", "empty", "error", "success", "validation")
_FORBIDDEN_PRODUCT_KEYS = {
    "api_contracts",
    "data_sources",
    "database",
    "schemas",
    "technical_architecture",
    "authorization",
    "permission_model",
    "role_assignments",
    "user_roles",
    "resourceKey",
    "policyKey",
    "operationKey",
    "dataRules",
    "dataPolicyBindings",
}
_REMOVED_PRODUCT_KEYS = {"assumptions", "risks"}
_DUPLICATED_PRODUCT_KEYS = {"frontend_pages"}
_PRODUCT_BEHAVIOR_TYPES = {"business", "navigation", "interface", "external", "sequence"}
_PRODUCT_STEP_TYPES = _PRODUCT_BEHAVIOR_TYPES - {"sequence"}
_MODEL_ROOT_KEYS = {
    "app",
    "business_flows",
    "pages",
    "product_acceptance_criteria",
}
_PRODUCT_PLAN_KEYS = {
    "schema_version",
    "version",
    "generated_at",
    "requirement_spec_sha256",
    "app",
    "business_flows",
    "pages",
    "authorizationTargets",
    "product_acceptance_criteria",
    "confirmation_status",
}
_MODEL_PAGE_KEYS = {
    "pageId",
    "name",
    "path",
    "module_id",
    "description",
    "goal",
    "information_items",
    "actions",
    "navigation_targets",
    "state_requirements",
    "acceptance_criteria",
}
_MODEL_INFORMATION_ITEM_KEYS = {"itemId", "label", "description"}
_MODEL_APP_KEYS = {"name", "summary"}
_MODEL_ACTION_KEYS = {
    "actionId",
    "name",
    "description",
    "requiresConfirmation",
    "behavior",
}
_LOWER_SNAKE_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _is_lower_snake_case(value: Any) -> bool:
    """判断页面和操作稳定标识是否符合当前权限资源键契约。"""

    return bool(_LOWER_SNAKE_CASE_PATTERN.fullmatch(str(value or "").strip()))


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从不可信列表中筛选对象项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _mapping_items(value: Any) -> list[dict[str, Any]]:
    """只接受当前模型契约中的真实 JSON 对象列表。"""

    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text_items(value: Any) -> list[str]:
    """把列表规范为去空白的字符串集合。"""

    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _exact_keys(value: Any, expected: set[str], location: str) -> list[str]:
    """校验模型对象只能包含示例声明的精确字段集合。"""

    if not isinstance(value, dict):
        return [f"{location} 必须是 JSON 对象。"]
    actual = set(value)
    errors: list[str] = []
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        errors.append(f"{location} 缺少字段：{'、'.join(missing)}。")
    if unexpected:
        errors.append(f"{location} 包含未声明字段：{'、'.join(unexpected)}。")
    return errors


def _model_behavior_errors(value: Any, location: str) -> list[str]:
    """按行为类型校验模型 behavior 与 sequence steps 的精确联合结构。"""

    if not isinstance(value, dict):
        return [f"{location} 必须是 JSON 对象。"]
    behavior_type = str(value.get("type") or "")
    expected_keys = {"type", "expectedResult"}
    if behavior_type == "navigation":
        expected_keys.add("targetPageId")
    elif behavior_type == "external":
        expected_keys.add("externalTarget")
    elif behavior_type == "sequence":
        expected_keys.add("steps")
    errors = _exact_keys(value, expected_keys, location)
    if behavior_type not in _PRODUCT_BEHAVIOR_TYPES:
        errors.append(f"{location}.type 必须是已声明的产品行为类型。")
    if behavior_type != "sequence":
        return errors
    steps = value.get("steps")
    if not isinstance(steps, list) or not steps:
        return [*errors, f"{location}.steps 必须是非空 JSON 数组。"]
    for index, step in enumerate(steps):
        step_location = f"{location}.steps[{index}]"
        step_type = str(step.get("type") or "") if isinstance(step, dict) else ""
        step_keys = {"stepId", "type", "expectedResult"}
        if step_type == "navigation":
            step_keys.add("targetPageId")
        elif step_type == "external":
            step_keys.add("externalTarget")
        errors.extend(_exact_keys(step, step_keys, step_location))
        if step_type not in _PRODUCT_STEP_TYPES:
            errors.append(f"{step_location}.type 必须是非 sequence 产品行为类型。")
    return errors


def validate_product_plan_model_output(
    agent_plan: dict[str, Any],
    requirement_spec: dict[str, Any],
) -> list[str]:
    """在归一化前严格校验模型 JSON 结构，拒绝重复页面树和自然语言猜字段。"""

    errors = _exact_keys(agent_plan, _MODEL_ROOT_KEYS, "ProductPlan 模型输出")
    errors.extend(_exact_keys(agent_plan.get("app"), _MODEL_APP_KEYS, "ProductPlan 模型输出.app"))
    for field in ("business_flows", "product_acceptance_criteria"):
        if not isinstance(agent_plan.get(field), list):
            errors.append(f"ProductPlan 模型输出.{field} 必须是 JSON 数组。")
    pages = agent_plan.get("pages")
    if not isinstance(pages, list):
        return [*errors, "ProductPlan 模型输出.pages 必须是 JSON 数组。"]
    expected_page_ids = [
        str(item.get("pageId") or item.get("id") or "").strip()
        for item in _dict_items(requirement_spec.get("pages"))
    ]
    actual_page_ids = [
        str(item.get("pageId") or "").strip()
        for item in _dict_items(pages)
    ]
    if actual_page_ids != expected_page_ids:
        errors.append("ProductPlan 模型输出.pages 必须按 RequirementSpec 顺序完整返回全部页面。")
    if any(not _is_lower_snake_case(page_id) for page_id in actual_page_ids):
        errors.append("ProductPlan 模型输出.pageId 必须全部为 lower_snake_case。")
    for page_index, page in enumerate(pages):
        location = f"ProductPlan 模型输出.pages[{page_index}]"
        errors.extend(_exact_keys(page, _MODEL_PAGE_KEYS, location))
        if not isinstance(page, dict):
            continue
        information_items = page.get("information_items")
        if not isinstance(information_items, list):
            errors.append(f"{location}.information_items 必须是 JSON 数组。")
        else:
            for item_index, item in enumerate(information_items):
                errors.extend(
                    _exact_keys(
                        item,
                        _MODEL_INFORMATION_ITEM_KEYS,
                        f"{location}.information_items[{item_index}]",
                    )
                )
        actions = page.get("actions")
        if not isinstance(actions, list):
            errors.append(f"{location}.actions 必须是 JSON 数组。")
        else:
            for action_index, action in enumerate(actions):
                action_location = f"{location}.actions[{action_index}]"
                errors.extend(_exact_keys(action, _MODEL_ACTION_KEYS, action_location))
                if isinstance(action, dict):
                    if not _is_lower_snake_case(action.get("actionId")):
                        errors.append(f"{action_location}.actionId 必须为 lower_snake_case。")
                    errors.extend(
                        _model_behavior_errors(action.get("behavior"), f"{action_location}.behavior")
                    )
        state_requirements = page.get("state_requirements")
        errors.extend(
            _exact_keys(state_requirements, set(_STATE_REQUIREMENT_KEYS), f"{location}.state_requirements")
        )
        for field in ("navigation_targets", "acceptance_criteria"):
            if not isinstance(page.get(field), list):
                errors.append(f"{location}.{field} 必须是 JSON 数组。")
    return errors


def _stable_action_id(page_id: str, value: Any, index: int) -> str:
    """为模型遗漏的页面操作生成稳定 actionId。"""

    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return normalized or f"{page_id}_action_{index}"


def _stable_item_id(page_id: str, value: Any, index: int) -> str:
    """为业务信息项生成页面内稳定的 itemId。"""

    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return f"{page_id}-{normalized or f'information-{index}'}"


def _stable_step_id(action_id: str, value: Any, index: int) -> str:
    """为组合产品行为中的步骤生成稳定 stepId。"""

    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return f"{action_id}-{normalized or f'step-{index}'}"


def _normalized_behavior_step(
    action_id: str,
    value: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    """规范单个产品可理解的行为步骤，不引入 endpoint 等技术字段。"""

    step_type = str(value.get("type") or "business").strip().lower()
    if step_type not in _PRODUCT_STEP_TYPES:
        step_type = "business"
    expected_result = str(
        value.get("expectedResult")
        or value.get("expected_result")
        or value.get("description")
        or "完成当前步骤并向用户呈现明确结果。"
    ).strip()
    step = {
        "stepId": str(value.get("stepId") or value.get("step_id") or "").strip()
        or _stable_step_id(action_id, expected_result, index),
        "type": step_type,
        "expectedResult": expected_result,
    }
    if step_type == "navigation":
        step["targetPageId"] = str(
            value.get("targetPageId") or value.get("target_page_id") or ""
        ).strip()
    elif step_type == "external":
        step["externalTarget"] = str(
            value.get("externalTarget") or value.get("external_target") or ""
        ).strip()
    return step


def _normalized_action_behavior(action_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """把产品动作规范为业务、导航、界面、外部或组合结果语义。"""

    source = item.get("behavior") if isinstance(item.get("behavior"), dict) else {}
    behavior_type = str(source.get("type") or item.get("behaviorType") or "").strip().lower()
    target_page_id = str(
        source.get("targetPageId")
        or item.get("targetPageId")
        or item.get("target_page_id")
        or ""
    ).strip()
    external_target = str(
        source.get("externalTarget")
        or item.get("externalTarget")
        or item.get("external_target")
        or ""
    ).strip()
    if not behavior_type:
        behavior_type = "navigation" if target_page_id else ("external" if external_target else "business")
    if behavior_type not in _PRODUCT_BEHAVIOR_TYPES:
        behavior_type = "business"
    expected_result = str(
        source.get("expectedResult")
        or item.get("expectedResult")
        or item.get("expected_result")
        or item.get("description")
        or "完成该产品操作并提供明确结果反馈。"
    ).strip()
    behavior: dict[str, Any] = {
        "type": behavior_type,
        "expectedResult": expected_result,
    }
    if behavior_type == "navigation":
        behavior["targetPageId"] = target_page_id
    elif behavior_type == "external":
        behavior["externalTarget"] = external_target
    elif behavior_type == "sequence":
        behavior["steps"] = [
            _normalized_behavior_step(action_id, step, index)
            for index, step in enumerate(_mapping_items(source.get("steps")), start=1)
        ]
    return behavior


def _normalized_information_items(page: dict[str, Any], value: Any) -> list[dict[str, str]]:
    """把页面业务信息统一为带稳定 itemId 的真实 JSON 对象。"""

    page_id = str(page.get("pageId") or "page")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(_mapping_items(value), start=1):
        label = str(item.get("label") or item.get("name") or item.get("description") or "").strip()
        if not label:
            continue
        item_id = str(item.get("itemId") or item.get("item_id") or item.get("id") or "").strip()
        item_id = item_id or _stable_item_id(page_id, label, index)
        if item_id in seen:
            continue
        seen.add(item_id)
        normalized.append(
            {
                "itemId": item_id,
                "label": label,
                "description": str(item.get("description") or label).strip(),
            }
        )
    if normalized:
        return normalized

    for index, text in enumerate(_text_items(value), start=1):
        item_id = _stable_item_id(page_id, text, index)
        if item_id not in seen:
            seen.add(item_id)
            normalized.append({"itemId": item_id, "label": text, "description": text})
    return normalized


def _normalized_actions(page: dict[str, Any], value: Any) -> list[dict[str, Any]]:
    """规范主动产品操作；纯浏览行为允许没有 action，不能用兜底查看操作伪造。"""

    page_id = str(page.get("pageId") or "page")
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(_mapping_items(value), start=1):
        name = str(item.get("name") or item.get("description") or f"页面操作 {index}").strip()
        action_id = str(item.get("actionId") or item.get("id") or "").strip()
        action_id = action_id or _stable_action_id(page_id, name, index)
        if action_id in seen:
            continue
        seen.add(action_id)
        action = {
            "actionId": action_id,
            "name": name,
            "description": str(item.get("description") or name).strip(),
            "requiresConfirmation": bool(item.get("requiresConfirmation", False)),
            "behavior": _normalized_action_behavior(action_id, item),
        }
        actions.append(action)
    return actions


def _normalized_pages(
    requirement_spec: dict[str, Any],
    agent_plan: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """保持 RequirementSpec 页面集合不变，仅合并产品级补充字段。"""

    agent_pages = {
        str(item.get("pageId") or item.get("id") or "").strip(): item
        for item in _dict_items((agent_plan or {}).get("pages"))
        if str(item.get("pageId") or item.get("id") or "").strip()
    }
    page_ids = {
        str(item.get("pageId") or item.get("id") or "").strip()
        for item in _dict_items(requirement_spec.get("pages"))
    }
    pages: list[dict[str, Any]] = []
    for source in _dict_items(requirement_spec.get("pages")):
        page_id = str(source.get("pageId") or source.get("id") or "").strip()
        if not page_id:
            continue
        supplement = agent_pages.get(page_id, {})
        information = _normalized_information_items(source, supplement.get("information_items"))
        if not information:
            information = _normalized_information_items(source, source.get("information_items"))
        if not information:
            fallback = str(source.get("description") or source.get("name") or "页面核心信息")
            information = _normalized_information_items(source, [fallback])
        actions = _normalized_actions(source, supplement.get("actions"))
        action_targets = [
            str(
                action.get("behavior", {}).get("targetPageId")
                if isinstance(action.get("behavior"), dict)
                else ""
            ).strip()
            for action in actions
            if isinstance(action.get("behavior"), dict)
            and str(action["behavior"].get("targetPageId") or "").strip()
        ]
        navigation = list(dict.fromkeys([
            target
            for target in [*_text_items(supplement.get("navigation_targets")), *action_targets]
            if target in page_ids
        ]))
        state_requirements = supplement.get("state_requirements")
        pages.append(
            {
                "pageId": page_id,
                "name": str(source.get("name") or page_id),
                "path": str(source.get("path") or f"/{page_id}"),
                "module_id": str(source.get("module_id") or "core"),
                "description": str(source.get("description") or source.get("name") or page_id),
                "goal": str(
                    supplement.get("goal")
                    or source.get("goal")
                    or source.get("description")
                    or source.get("name")
                    or page_id
                ),
                "information_items": information,
                "actions": actions,
                "navigation_targets": navigation,
                "state_requirements": (
                    dict(state_requirements)
                    if isinstance(state_requirements, dict)
                    else {
                        "loading": "加载业务数据时展示明确进度。",
                        "empty": "无数据时解释当前状态并提供下一步入口。",
                        "error": "失败时展示原因和重试入口。",
                        "success": "成功操作提供结果反馈。",
                        "validation": "提交前指出缺失或无效输入。",
                    }
                ),
                "acceptance_criteria": product_acceptance_criteria(
                    supplement.get("acceptance_criteria")
                )
                or ["页面目标和核心操作可以由目标角色完成。"],
            }
        )
    return pages


def _authorization_target_key(value: Any) -> str:
    """把业务名称压缩为确定性匹配键，不从相近词推断权限目标。"""

    return re.sub(r"[^\w]+", "", str(value or "").casefold())


def _authorization_targets(
    requirement_spec: dict[str, Any],
    pages: list[dict[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    """把已确认页面/操作候选确定性映射到 ProductPlan 的稳定目标。"""

    authorization = requirement_spec.get("authorization_requirements")
    authorization = authorization if isinstance(authorization, dict) else {}
    if authorization.get("enabled") is not True:
        return {"pageRules": [], "operationRules": []}

    page_by_name: dict[str, list[str]] = {}
    action_by_name: dict[str, list[dict[str, str]]] = {}
    for page in pages:
        page_id = str(page.get("pageId") or "").strip()
        page_key = _authorization_target_key(page.get("name"))
        if page_id and page_key:
            page_by_name.setdefault(page_key, []).append(page_id)
        for action in _dict_items(page.get("actions")):
            action_id = str(action.get("actionId") or "").strip()
            action_key = _authorization_target_key(action.get("name"))
            if page_id and action_id and action_key:
                action_by_name.setdefault(action_key, []).append(
                    {"pageId": page_id, "actionId": action_id}
                )

    def mapped_rules(field_name: str, targets: dict[str, list[str]], target_field: str) -> list[dict[str, str]]:
        """只在名称一对一匹配时保留规则目标，歧义交由确认校验显式阻断。"""

        result: list[dict[str, str]] = []
        for rule in _dict_items(authorization.get(field_name)):
            rule_id = str(rule.get("ruleId") or "").strip()
            candidates = targets.get(_authorization_target_key(rule.get("name")), [])
            if rule_id and len(candidates) == 1:
                result.append({"ruleId": rule_id, target_field: candidates[0]})
        return result

    operation_rules: list[dict[str, str]] = []
    for rule in _dict_items(authorization.get("restrictedOperations")):
        rule_id = str(rule.get("ruleId") or "").strip()
        candidates = action_by_name.get(_authorization_target_key(rule.get("name")), [])
        if rule_id and len(candidates) == 1:
            operation_rules.append({"ruleId": rule_id, **candidates[0]})
    return {
        "pageRules": mapped_rules("restrictedPages", page_by_name, "pageId"),
        # 操作 ID 只在页面内唯一；权限目标必须带父页面才能成为后续资源绑定的唯一坐标。
        "operationRules": operation_rules,
    }


def _authorization_resource_candidate_errors(product_plan: dict[str, Any]) -> list[str]:
    """校验 ProductPlan 权限目标可导出的全局资源键，不在本阶段写入资源键。"""

    targets = product_plan.get("authorizationTargets")
    targets = targets if isinstance(targets, dict) else {}
    candidates: dict[str, list[str]] = {"system_authorization_management": ["系统资源"]}
    for mapping in _dict_items(targets.get("pageRules")):
        page_id = str(mapping.get("pageId") or "").strip()
        if page_id:
            candidates.setdefault(page_id, []).append(f"页面 {page_id}")
    for mapping in _dict_items(targets.get("operationRules")):
        page_id = str(mapping.get("pageId") or "").strip()
        action_id = str(mapping.get("actionId") or "").strip()
        if page_id and action_id:
            candidates.setdefault(f"{page_id}_{action_id}", []).append(
                f"操作 {page_id}/{action_id}"
            )
    return [
        "ProductPlan 权限资源候选发生跨类型碰撞："
        + f"{resource_key}（{'、'.join(locations)}）。"
        for resource_key, locations in candidates.items()
        if len(locations) > 1
    ]


def authorization_operation_action_coverage(
    requirement_spec: dict[str, Any],
    product_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """列出无法唯一落到 ProductPlan action 的已确认权限操作规则。"""

    authorization = requirement_spec.get("authorization_requirements")
    authorization = authorization if isinstance(authorization, dict) else {}
    if authorization.get("enabled") is not True:
        return []

    action_candidates: dict[str, list[dict[str, str]]] = {}
    for page in _dict_items(product_plan.get("pages")):
        page_id = str(page.get("pageId") or "").strip()
        page_name = str(page.get("name") or page_id).strip()
        for action in _dict_items(page.get("actions")):
            action_id = str(action.get("actionId") or "").strip()
            action_name = str(action.get("name") or "").strip()
            action_key = _authorization_target_key(action_name)
            if page_id and action_id and action_key:
                action_candidates.setdefault(action_key, []).append(
                    {
                        "pageId": page_id,
                        "pageName": page_name,
                        "actionId": action_id,
                        "actionName": action_name,
                    }
                )

    uncovered: list[dict[str, Any]] = []
    for rule in _dict_items(authorization.get("restrictedOperations")):
        rule_id = str(rule.get("ruleId") or "").strip()
        name = str(rule.get("name") or "").strip()
        candidates = action_candidates.get(_authorization_target_key(name), [])
        if rule_id and name and len(candidates) != 1:
            uncovered.append(
                {
                    "ruleId": rule_id,
                    "name": name,
                    "description": str(rule.get("description") or name).strip(),
                    "candidates": candidates,
                    "reason": "missing" if not candidates else "ambiguous",
                }
            )
    return uncovered


def authorization_operation_action_coverage_errors(
    requirement_spec: dict[str, Any],
    product_plan: dict[str, Any],
) -> list[str]:
    """把权限操作覆盖缺口转换为可回灌给产品规划模型的具体诊断。"""

    errors: list[str] = []
    for item in authorization_operation_action_coverage(requirement_spec, product_plan):
        name = item["name"]
        if item["reason"] == "missing":
            errors.append(f"缺少受限操作 action：{name}。必须生成一个 name 完全等于“{name}”的唯一 action。")
        else:
            action_ids = "、".join(candidate["actionId"] for candidate in item["candidates"])
            errors.append(f"受限操作 action 重复：{name}（{action_ids}）。必须只保留一个 name 为“{name}”的 action。")
    return errors


def create_product_plan(
    requirement_spec: dict[str, Any],
    *,
    agent_plan: dict[str, Any] | None = None,
    existing_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从已确认 RequirementSpec 构造产品确认用 ProductPlan。"""

    app_info = requirement_spec.get("app_info")
    app_info = app_info if isinstance(app_info, dict) else {}
    pages = _normalized_pages(requirement_spec, agent_plan)
    plan = {
        "schema_version": PRODUCT_PLAN_SCHEMA_VERSION,
        "version": str((existing_plan or {}).get("version") or "0.1.0"),
        "generated_at": datetime.now(UTC).isoformat(),
        "requirement_spec_sha256": requirement_spec_sha256(requirement_spec),
        "app": {
            "name": str(app_info.get("name") or "未命名应用"),
            "summary": str(app_info.get("summary") or requirement_spec.get("summary") or ""),
        },
        "business_flows": _dict_items(requirement_spec.get("business_flows")),
        "pages": pages,
        # 映射只追踪已确认业务规则到产品稳定目标，不包含角色、资源键或策略键。
        "authorizationTargets": _authorization_targets(requirement_spec, pages),
        "product_acceptance_criteria": product_acceptance_criteria(
            (agent_plan or {}).get("product_acceptance_criteria")
        )
        or product_acceptance_criteria(requirement_spec.get("acceptance_criteria")),
        "confirmation_status": "pending_user_confirmation",
    }
    return plan


def requirement_spec_sha256(requirement_spec: dict[str, Any]) -> str:
    """计算 RequirementSpec 当前确认内容的稳定摘要，供联合需求文档绑定使用。"""

    return hashlib.sha256(
        json.dumps(
            requirement_spec,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def validate_product_plan(product_plan: dict[str, Any], requirement_spec: dict[str, Any]) -> list[str]:
    """校验 ProductPlan v4 的结构、需求边界和稳定引用均闭合。"""

    expected = [
        str(item.get("pageId") or item.get("id") or "").strip()
        for item in _dict_items(requirement_spec.get("pages"))
    ]
    actual = [str(item.get("pageId") or "").strip() for item in _dict_items(product_plan.get("pages"))]
    errors: list[str] = []
    errors.extend(_exact_keys(product_plan, _PRODUCT_PLAN_KEYS, "ProductPlan"))
    if product_plan.get("schema_version") != PRODUCT_PLAN_SCHEMA_VERSION:
        errors.append(f"ProductPlan.schema_version 必须为 {PRODUCT_PLAN_SCHEMA_VERSION}。")
    if str(product_plan.get("requirement_spec_sha256") or "") != requirement_spec_sha256(requirement_spec):
        errors.append("ProductPlan.requirement_spec_sha256 必须绑定当前 RequirementSpec。")
    forbidden_keys = sorted(_FORBIDDEN_PRODUCT_KEYS.intersection(product_plan))
    if forbidden_keys:
        errors.append("ProductPlan 不得包含技术规划字段：" + "、".join(forbidden_keys) + "。")
    removed_keys = sorted(_REMOVED_PRODUCT_KEYS.intersection(product_plan))
    if removed_keys:
        errors.append("ProductPlan 不得包含产品假设或产品风险字段：" + "、".join(removed_keys) + "。")
    if not isinstance(product_plan.get("app"), dict):
        errors.append("ProductPlan.app 必须是 JSON 对象。")
    for key in ("business_flows", "pages"):
        if not isinstance(product_plan.get(key), list):
            errors.append(f"ProductPlan.{key} 必须是 JSON 数组。")
    duplicated_keys = sorted(_DUPLICATED_PRODUCT_KEYS.intersection(product_plan))
    if duplicated_keys:
        errors.append("ProductPlan 不得包含 pages 的重复投影字段：" + "、".join(duplicated_keys) + "。")
    acceptance_criteria = product_plan.get("product_acceptance_criteria")
    if not isinstance(acceptance_criteria, list) or any(
        not isinstance(item, str) for item in acceptance_criteria
    ):
        errors.append("ProductPlan.product_acceptance_criteria 必须是字符串数组。")
    if expected != actual:
        errors.append("ProductPlan.pages 必须与 RequirementSpec.pages 一一对应且保持顺序。")
    if any(not _is_lower_snake_case(page_id) for page_id in actual):
        errors.append("ProductPlan.pages.pageId 必须全部为 lower_snake_case。")
    page_ids = set(actual)
    authorization = requirement_spec.get("authorization_requirements")
    authorization = authorization if isinstance(authorization, dict) else {}
    authorization_targets = product_plan.get("authorizationTargets")
    if not isinstance(authorization_targets, dict):
        errors.append("ProductPlan.authorizationTargets 必须是 JSON 对象。")
        authorization_targets = {}
    if set(authorization_targets) != {"pageRules", "operationRules"}:
        errors.append("ProductPlan.authorizationTargets 只能包含 pageRules 和 operationRules。")
    for field_name in ("pageRules", "operationRules"):
        if not isinstance(authorization_targets.get(field_name), list):
            errors.append(f"ProductPlan.authorizationTargets.{field_name} 必须是 JSON 数组。")
    for page in _dict_items(product_plan.get("pages")):
        page_id = str(page.get("pageId") or "")
        errors.extend(_exact_keys(page, _MODEL_PAGE_KEYS, f"ProductPlan.pages[{page_id or 'unknown'}]"))
        if page_id == "system_authorization_management" or str(page.get("path") or "") == "/roles":
            errors.append("ProductPlan.pages 不得包含固定权限管理页面 /roles。")
        for key in ("name", "path", "module_id", "description", "goal"):
            if not str(page.get(key) or "").strip():
                errors.append(f"页面 {page_id or 'unknown'} 缺少非空字段 {key}。")
        information_items = _dict_items(page.get("information_items"))
        information_ids = [str(item.get("itemId") or "").strip() for item in information_items]
        if not information_ids or any(not item_id for item_id in information_ids):
            errors.append(f"页面 {page_id} 的 information_items 必须是含非空 itemId 的对象列表。")
        elif len(information_ids) != len(set(information_ids)):
            errors.append(f"页面 {page_id} 的 information_items.itemId 必须页面内唯一。")
        action_ids = [
            str(item.get("actionId") or "").strip()
            for item in _dict_items(page.get("actions"))
        ]
        if any(not action_id for action_id in action_ids) or len(action_ids) != len(set(action_ids)):
            errors.append(f"页面 {page_id} 的 actionId 必须非空且唯一；纯展示页面可以没有 actions。")
        elif any(not _is_lower_snake_case(action_id) for action_id in action_ids):
            errors.append(f"页面 {page_id} 的 actionId 必须为 lower_snake_case。")
        for action in _dict_items(page.get("actions")):
            errors.extend(
                _exact_keys(action, _MODEL_ACTION_KEYS, f"页面 {page_id} 的 action")
            )
            for key in ("name", "description"):
                if not str(action.get(key) or "").strip():
                    errors.append(f"页面 {page_id} 的 action 缺少非空字段 {key}。")
            if not isinstance(action.get("requiresConfirmation"), bool):
                errors.append(f"页面 {page_id} 的 action.requiresConfirmation 必须是 boolean。")
            behavior = action.get("behavior") if isinstance(action.get("behavior"), dict) else {}
            behavior_type = str(behavior.get("type") or "").strip()
            if behavior_type not in _PRODUCT_BEHAVIOR_TYPES:
                errors.append(f"页面 {page_id} 的 action.behavior.type 不合法。")
            if not str(behavior.get("expectedResult") or "").strip():
                errors.append(f"页面 {page_id} 的 action.behavior.expectedResult 不能为空。")
            target_page_id = str(behavior.get("targetPageId") or "").strip()
            if behavior_type == "navigation" and not target_page_id:
                errors.append(f"页面 {page_id} 的导航 action 必须声明 targetPageId。")
            if target_page_id and target_page_id not in page_ids:
                errors.append(f"页面 {page_id} 的操作引用了不存在的 targetPageId。")
            if target_page_id and target_page_id not in _text_items(page.get("navigation_targets")):
                errors.append(f"页面 {page_id} 的跳转操作未同步到 navigation_targets。")
            if behavior_type == "external" and not str(behavior.get("externalTarget") or "").strip():
                errors.append(f"页面 {page_id} 的外部 action 必须声明 externalTarget。")
            if behavior_type == "sequence":
                steps = _dict_items(behavior.get("steps"))
                step_ids = [str(step.get("stepId") or "").strip() for step in steps]
                if not steps or any(not step_id for step_id in step_ids) or len(step_ids) != len(set(step_ids)):
                    errors.append(f"页面 {page_id} 的组合 action 必须包含唯一非空 stepId。")
                for step in steps:
                    step_type = str(step.get("type") or "")
                    if step_type not in _PRODUCT_STEP_TYPES:
                        errors.append(f"页面 {page_id} 的组合 action 包含非法产品步骤类型。")
                    if not str(step.get("expectedResult") or "").strip():
                        errors.append(f"页面 {page_id} 的组合 action 步骤缺少 expectedResult。")
        if any(target not in page_ids for target in _text_items(page.get("navigation_targets"))):
            errors.append(f"页面 {page_id} 引用了不存在的跳转目标。")
        forbidden_page_keys = {
            "allowed_roles",
            "allowedRoleIds",
            "authorization",
            "permissions",
            "roleIds",
        }.intersection(page)
        if forbidden_page_keys:
            errors.append(
                f"页面 {page_id} 不得包含角色或授权字段：" + "、".join(sorted(forbidden_page_keys)) + "。"
            )
        state_requirements = page.get("state_requirements")
        if not isinstance(state_requirements, dict) or any(
            not str(state_requirements.get(key) or "").strip() for key in _STATE_REQUIREMENT_KEYS
        ):
            errors.append(f"页面 {page_id} 的 state_requirements 必须完整覆盖五种产品状态。")
    target_specs = (("restrictedPages", "pageRules", {"ruleId", "pageId"}),
                    ("restrictedOperations", "operationRules", {"ruleId", "pageId", "actionId"}))
    action_targets = {
        (str(page.get("pageId") or "").strip(), str(action.get("actionId") or "").strip())
        for page in _dict_items(product_plan.get("pages"))
        for action in _dict_items(page.get("actions"))
    }
    for requirement_field, mapping_field, expected_mapping_keys in target_specs:
        expected_rule_ids = {
            str(rule.get("ruleId") or "").strip()
            for rule in _dict_items(authorization.get(requirement_field))
            if str(rule.get("ruleId") or "").strip()
        }
        mappings = _dict_items(authorization_targets.get(mapping_field))
        actual_rule_ids = [str(item.get("ruleId") or "").strip() for item in mappings]
        if set(actual_rule_ids) != expected_rule_ids or len(actual_rule_ids) != len(set(actual_rule_ids)):
            errors.append(
                f"ProductPlan.authorizationTargets.{mapping_field} 必须与已确认 {requirement_field} 一一对应。"
            )
        for mapping in mappings:
            if set(mapping) != expected_mapping_keys:
                errors.append(
                    f"ProductPlan.authorizationTargets.{mapping_field} 映射字段必须为 "
                    + "、".join(sorted(expected_mapping_keys))
                    + "。"
                )
                continue
            page_id = str(mapping.get("pageId") or "").strip()
            if page_id not in page_ids:
                errors.append(
                    f"ProductPlan.authorizationTargets.{mapping_field} 引用了不存在的 pageId。"
                )
            if mapping_field == "operationRules":
                action_id = str(mapping.get("actionId") or "").strip()
                if (page_id, action_id) not in action_targets:
                    errors.append(
                        "ProductPlan.authorizationTargets.operationRules 引用了不存在的 pageId/actionId。"
                    )
    if authorization.get("enabled") is True:
        errors.extend(_authorization_resource_candidate_errors(product_plan))
    return errors


def require_current_product_plan(
    value: Any,
    requirement_spec: dict[str, Any],
) -> dict[str, Any]:
    """要求下游只消费当前 pages-only ProductPlan，拒绝历史格式和无效快照。"""

    if not isinstance(value, dict):
        raise ValueError("缺少当前 ProductPlan。")
    if value.get("schema_version") != PRODUCT_PLAN_SCHEMA_VERSION or "frontend_pages" in value:
        raise ValueError(
            f"当前流程只接受 {PRODUCT_PLAN_SCHEMA_VERSION}，不读取或迁移历史 ProductPlan。"
        )
    errors = validate_product_plan(value, requirement_spec)
    if errors:
        raise ValueError("当前 ProductPlan 不符合正式契约：" + "；".join(errors))
    return value
