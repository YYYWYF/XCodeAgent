from __future__ import annotations

import json
from typing import Any

from app.agents.design_conversation.fallback import (
    fallback_design_conversation_decision,
    invalid_model_decision,
)
from app.agents.design_conversation.models import DesignConversationDecision
from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.utils.model_output import extract_json_object


def classify_design_conversation(
    request: str,
    *,
    requirement_spec: dict[str, Any] | None,
    product_plan: dict[str, Any] | None,
    ui_designs: dict[str, Any] | None,
    settings: Settings | None = None,
) -> DesignConversationDecision:
    """调用独立 ChatModel 识别用户意图和产品语义层。"""

    prompt = _classification_prompt(
        request,
        requirement_spec=requirement_spec,
        product_plan=product_plan,
        ui_designs=ui_designs,
    )
    try:
        active_settings = settings or Settings.from_env()
        result = create_chat_model(active_settings).invoke(prompt)
        content = _coerce_content_text(getattr(result, "content", "")) or ""
        parsed = extract_json_object(content)
    except Exception:
        # 模型调用失败时执行保守语义兜底，越界信号始终优先。
        return fallback_design_conversation_decision(
            request,
            requirement_spec=requirement_spec,
            product_plan=product_plan,
        )
    try:
        decision = DesignConversationDecision.model_validate(parsed)
    except Exception:
        # 模型返回未知字段、技术节点或非法组合时直接拒绝，不再按用户文本授予修改能力。
        return invalid_model_decision(parsed)
    return _normalize_decision(decision, product_plan)


def _classification_prompt(
    request: str,
    *,
    requirement_spec: dict[str, Any] | None,
    product_plan: dict[str, Any] | None,
    ui_designs: dict[str, Any] | None,
) -> str:
    """构建产品语义分类提示，只提供紧凑产品事实而不加载源码。"""

    context = {
        "requirement": _requirement_summary(requirement_spec),
        "product": _product_summary(product_plan),
        "ui": _ui_summary(ui_designs),
    }
    return (
        "You are AIStudio's Product Conversation Coordinator. Return one JSON object and no markdown.\n"
        "You only interpret the user's intent and product semantic level. Never output Graph nodes and "
        "never claim to edit files, code, TechnicalPlan, APIs, schemas, databases, builds, or tests.\n"
        "Allowed intent values: chat, read_only, requirement_change, ui_change, clarification, "
        "out_of_scope. Allowed change_level values: requirement, product_behavior, ui, none.\n"
        "Use requirement_change + requirement for product goals, scope, roles, modules, page existence, "
        "new/deleted pages, business flows, business information, and business permissions.\n"
        "Use requirement_change + product_behavior for page goals, actions, navigation, visible results, "
        "states, and product acceptance behavior when the page inventory remains valid.\n"
        "Use ui_change + ui only for layout, visual hierarchy, controls, color, typography, spacing, "
        "theme, responsive presentation, or presentation of an existing interaction.\n"
        "Use read_only for questions answerable from the bounded product context and put the answer in "
        "response. If the context is insufficient, say so explicitly. Use chat for casual conversation.\n"
        "Use clarification when the product-layer request is ambiguous and put exactly one useful "
        "question in clarification_question.\n"
        "Use out_of_scope when the user explicitly asks to change a TechnicalPlan, implementation "
        "contract, source code, dependencies, or to execute commands/builds/tests. Choose "
        "suggested_phase=planning, development, or test from the requested action. Do not classify a "
        "request as out_of_scope merely because it mentions API, database, test users, or other "
        "technical nouns: decide what product fact or presentation the user actually wants changed.\n"
        "If a request mixes any product change with any out-of-scope work, classify the whole request "
        "as out_of_scope; do not execute only the product portion.\n"
        "affected_page_ids may contain only pageIds from the current ProductPlan. Leave it empty when "
        "the affected page cannot be determined safely. Product changes use suggested_phase=none; "
        "non-change intents use change_level=none.\n"
        "Output shape: {\"intent\":\"chat|read_only|requirement_change|ui_change|clarification|out_of_scope\","
        "\"change_level\":\"requirement|product_behavior|ui|none\",\"reason\":\"short reason\","
        "\"affected_page_ids\":[],\"response\":\"\",\"suggested_phase\":"
        "\"planning|development|test|none\",\"clarification_question\":\"\"}.\n\n"
        f"Current compact product context:\n{json.dumps(context, ensure_ascii=False)}\n\n"
        f"Latest user input:\n{request}"
    )


def _requirement_summary(spec: dict[str, Any] | None) -> dict[str, Any]:
    """提取分类所需的需求概要，避免把完整正式文档重复塞给模型。"""

    if not isinstance(spec, dict):
        return {}
    app_info = spec.get("app_info") if isinstance(spec.get("app_info"), dict) else {}
    return {
        "name": app_info.get("name"),
        "confirmation_status": spec.get("confirmation_status"),
        "roles": _id_name_pairs(spec.get("user_roles"), "id"),
        "modules": _id_name_pairs(spec.get("feature_modules"), "id"),
        "pages": _id_name_pairs(spec.get("pages"), "pageId"),
        "flows": _id_name_pairs(spec.get("business_flows"), "id"),
    }


def _product_summary(plan: dict[str, Any] | None) -> dict[str, Any]:
    """提取页面及操作身份，供 Coordinator 判断产品行为和 UI 边界。"""

    if not isinstance(plan, dict):
        return {}
    pages: list[dict[str, Any]] = []
    for page in plan.get("pages", []):
        if not isinstance(page, dict):
            continue
        pages.append(
            {
                "pageId": page.get("pageId"),
                "name": page.get("name"),
                "goal": page.get("goal"),
                "actions": _id_name_pairs(page.get("actions"), "actionId"),
                "information_items": _id_name_pairs(
                    page.get("information_items"), "itemId"
                ),
                "states": _id_name_pairs(page.get("states"), "stateId"),
            }
        )
    return {
        "confirmation_status": plan.get("confirmation_status"),
        "pages": pages,
    }


def _ui_summary(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """提取 UiManifest 状态与页面身份，不读取或暴露 React 源码。"""

    if not isinstance(manifest, dict):
        return {}
    return {
        "confirmation_status": manifest.get("confirmation_status"),
        "pages": [
            {
                "pageId": page.get("pageId"),
                "status": page.get("status"),
            }
            for page in manifest.get("pages", [])
            if isinstance(page, dict)
        ],
    }


def _id_name_pairs(value: Any, id_field: str) -> list[dict[str, Any]]:
    """把正式条目压缩为稳定 ID 与名称对。"""

    return [
        {"id": item.get(id_field) or item.get("id"), "name": item.get("name")}
        for item in value or []
        if isinstance(item, dict)
    ][:100]


def _normalize_decision(
    decision: DesignConversationDecision,
    product_plan: dict[str, Any] | None,
) -> DesignConversationDecision:
    """过滤未知页面 ID，并清理 Coordinator 返回的展示文本。"""

    known_ids = {
        str(page.get("pageId") or "").strip()
        for page in (product_plan or {}).get("pages", [])
        if isinstance(page, dict) and str(page.get("pageId") or "").strip()
    }
    affected = list(
        dict.fromkeys(
            page_id
            for raw in decision.affected_page_ids
            if (page_id := str(raw).strip()) and page_id in known_ids
        )
    )
    response = decision.response.strip()
    clarification_question = decision.clarification_question.strip()
    if decision.intent == "chat" and not response:
        response = "这条消息不需要调整需求、产品行为或 UI 设计。"
    if decision.intent == "read_only" and not response:
        response = "当前产品设计中没有足够信息确定这一点。"
    if decision.intent == "clarification" and not clarification_question:
        clarification_question = "请再说明你希望调整的产品事实、页面行为或 UI 表现。"
    return decision.model_copy(
        update={
            "affected_page_ids": affected,
            "response": response,
            "clarification_question": clarification_question,
        }
    )
