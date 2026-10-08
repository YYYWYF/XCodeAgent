from __future__ import annotations

import json
from typing import Any

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.agents.design_conversation.fallback import (
    fallback_design_conversation_decision,
    invalid_model_decision,
)
from app.agents.design_conversation.models import DesignConversationDecision
from app.config import Settings
from app.services.access_control_intent import (
    has_explicit_business_access_control_change,
)
from app.utils.model_output import extract_json_object


def classify_design_conversation(
    request: str,
    *,
    requirement_spec: dict[str, Any] | None,
    product_plan: dict[str, Any] | None,
    ui_designs: dict[str, Any] | None,
    settings: Settings | None = None,
) -> DesignConversationDecision:
    """调用产品语义 Coordinator，返回与确定性 Policy 相同的契约。"""

    if has_explicit_business_access_control_change(request):
        # 受控页面、操作或角色访问权属于 RequirementSpec 事实，不能交给路由模型猜测层级。
        return DesignConversationDecision(
            intent="requirement_change",
            change_level="requirement",
            reason="输入明确调整角色访问控制，必须先修订需求权限规则。",
            affected_page_ids=fallback_design_conversation_decision(
                request,
                requirement_spec=requirement_spec,
                product_plan=product_plan,
            ).affected_page_ids,
            response="",
        )
    active_settings = settings or Settings.from_env()
    prompt = _classification_prompt(
        request,
        requirement_spec=requirement_spec,
        product_plan=product_plan,
        ui_designs=ui_designs,
    )
    try:
        result = create_chat_model(active_settings).invoke(prompt)
        content = _coerce_content_text(getattr(result, "content", "")) or ""
        parsed = extract_json_object(content)
        try:
            decision = DesignConversationDecision.model_validate(parsed)
        except Exception:
            # 模型已回答却返回旧 target/技术节点时必须零写入，不能用关键词兜底提权。
            return invalid_model_decision(parsed)
        return _normalize_decision(decision, product_plan)
    except Exception:
        # 网络或模型不可用时按当前语义契约保守兜底，不能返回旧 target 对象。
        return fallback_design_conversation_decision(
            request,
            requirement_spec=requirement_spec,
            product_plan=product_plan,
        )


def _classification_prompt(
    request: str,
    *,
    requirement_spec: dict[str, Any] | None,
    product_plan: dict[str, Any] | None,
    ui_designs: dict[str, Any] | None,
) -> str:
    """构建产品语义分类提示，只提供紧凑产品事实而不加载 TSX 正文。"""

    context = {
        "requirement": _requirement_summary(requirement_spec),
        "product": _product_summary(product_plan),
        "ui": _ui_summary(ui_designs),
    }
    return (
        "You are the product-stage conversation coordinator for XCodeAgent.\n"
        "Classify the user's product intent, not a workflow node. "
        "Return one JSON object and no markdown.\n"
        "intent must be chat, read_only, requirement_change, ui_change, clarification, or out_of_scope.\n"
        "change_level must be requirement, product_behavior, ui, or none.\n"
        "Use requirement_change + requirement for goals, scope, roles, modules, page inventory, "
        "business flows, or required business information.\n"
        "Use requirement_change + product_behavior for page actions, navigation behavior, "
        "states, visible outcomes, or acceptance criteria when requirements remain valid.\n"
        "Use ui_change + ui only for visual layout, sidebar appearance, styling, controls, "
        "responsive/theme presentation, or local interaction treatment without new product facts.\n"
        "For questions use read_only + none; for small talk chat + none; if unclear use "
        "clarification + none; for implementation, code, API, database or testing requests "
        "use out_of_scope + none and set suggested_phase. Never output a Graph node or target.\n"
        "affected_page_ids may contain only pageIds from the current ProductPlan. Leave it empty when "
        "the affected page cannot be determined safely. response is required only for chat.\n"
        "Output shape: {\"intent\":\"ui_change\",\"change_level\":\"ui\","
        "\"reason\":\"short reason\",\"affected_page_ids\":[],\"response\":\"\","
        "\"suggested_phase\":\"none\",\"clarification_question\":\"\"}.\n\n"
        f"Current compact design context:\n{json.dumps(context, ensure_ascii=False)}\n\n"
        f"Latest user input:\n{request}"
    )


def _requirement_summary(spec: dict[str, Any] | None) -> dict[str, Any]:
    """提取路由所需的需求概要，避免把完整正式文档重复塞给模型。"""

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
    """提取页面及操作身份，供路由 Agent 判断产品与 UI 边界。"""

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
                "actions": _id_name_pairs(page.get("actions"), "actionId"),
                "information_items": _id_name_pairs(
                    page.get("information_items"), "itemId"
                ),
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
    """过滤未知页面 ID，并补齐只读/闲聊的安全回复。"""

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
    if decision.intent in {"chat", "read_only"} and not response:
        response = "当前产品设计中没有足够信息确定这一点。"
    return decision.model_copy(
        update={"affected_page_ids": affected, "response": response}
    )
