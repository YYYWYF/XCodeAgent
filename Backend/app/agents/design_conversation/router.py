from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.utils.model_output import extract_json_object


DesignChangeTarget = Literal[
    "requirements",
    "product_planning",
    "ui_confirmation",
    "chat",
]


class DesignConversationDecision(BaseModel):
    """设计阶段对话 Agent 的稳定路由结果。"""

    model_config = ConfigDict(extra="forbid")

    target: DesignChangeTarget
    reason: str = Field(min_length=1, max_length=500)
    affected_page_ids: list[str] = Field(default_factory=list, max_length=100)
    response: str = Field(default="", max_length=2000)


def classify_design_conversation(
    request: str,
    *,
    requirement_spec: dict[str, Any] | None,
    product_plan: dict[str, Any] | None,
    ui_designs: dict[str, Any] | None,
    settings: Settings | None = None,
) -> DesignConversationDecision:
    """调用独立 ChatModel 判断最早需要回退的真实设计节点。"""

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
        decision = DesignConversationDecision.model_validate(parsed)
        return _normalize_decision(decision, product_plan)
    except Exception:
        # 路由 Agent 失败时仍保持可用，并按“越上游越安全”的确定性规则兜底。
        return _fallback_decision(request, product_plan)


def _classification_prompt(
    request: str,
    *,
    requirement_spec: dict[str, Any] | None,
    product_plan: dict[str, Any] | None,
    ui_designs: dict[str, Any] | None,
) -> str:
    """构建设计节点路由提示，只提供紧凑产品事实而不加载 TSX 正文。"""

    context = {
        "requirement": _requirement_summary(requirement_spec),
        "product": _product_summary(product_plan),
        "ui": _ui_summary(ui_designs),
    }
    return (
        "You are the dedicated design-conversation routing agent for XCodeAgent.\n"
        "Your only job is to decide the earliest formal design artifact node that must be revised. "
        "Return one JSON object and no markdown.\n"
        "Allowed target values: requirements, product_planning, ui_confirmation, chat.\n"
        "Choose requirements for changes to product goals, scope, roles, modules, page inventory, "
        "business flows, or required business information.\n"
        "Choose product_planning when the fixed RequirementSpec remains valid but page goals, user "
        "actions, navigation, visible outcomes, states, or product acceptance criteria must change.\n"
        "Choose ui_confirmation only for visual layout, hierarchy, styling, controls, responsive/theme "
        "presentation, or local interaction treatment that does not change product facts.\n"
        "Choose chat only when no formal artifact needs to change.\n"
        "If one request spans several levels, choose the earliest node: requirements before "
        "product_planning before ui_confirmation. Never choose technical planning, APIs, schemas, "
        "databases, code generation, or implementation nodes.\n"
        "affected_page_ids may contain only pageIds from the current ProductPlan. Leave it empty when "
        "the affected page cannot be determined safely. response is required only for chat.\n"
        "Output shape: {\"target\":\"requirements|product_planning|ui_confirmation|chat\","
        "\"reason\":\"short reason\",\"affected_page_ids\":[],\"response\":\"\"}.\n\n"
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
    """过滤未知页面 ID，并补齐 chat 的安全回复。"""

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
    if decision.target == "chat" and not response:
        response = "这条消息不需要调整需求、产品规划或 UI 设计。"
    return decision.model_copy(
        update={"affected_page_ids": affected, "response": response}
    )


def _fallback_decision(
    request: str,
    product_plan: dict[str, Any] | None,
) -> DesignConversationDecision:
    """模型不可用时按关键词选择最早设计节点，并尽量识别页面 ID。"""

    text = request.strip().lower()
    requirement_signals = (
        "需求",
        "范围",
        "角色",
        "模块",
        "新增页面",
        "删除页面",
        "业务流程",
        "不需要这个页面",
    )
    product_signals = (
        "产品规划",
        "操作",
        "跳转",
        "验收标准",
        "加载状态",
        "空状态",
        "业务结果",
    )
    ui_signals = (
        "ui",
        "界面",
        "布局",
        "样式",
        "颜色",
        "弹窗",
        "组件",
        "响应式",
        "深色",
        "浅色",
    )
    if any(signal in text for signal in requirement_signals):
        target: DesignChangeTarget = "requirements"
    elif any(signal in text for signal in product_signals):
        target = "product_planning"
    elif any(signal in text for signal in ui_signals):
        target = "ui_confirmation"
    else:
        target = "chat"
    page_ids = _mentioned_page_ids(text, product_plan)
    return DesignConversationDecision(
        target=target,
        reason="路由模型不可用，已使用设计阶段保守规则判断。",
        affected_page_ids=page_ids,
        response=("这条消息暂未识别为正式设计产物调整。" if target == "chat" else ""),
    )


def _mentioned_page_ids(
    request: str,
    product_plan: dict[str, Any] | None,
) -> list[str]:
    """在兜底路径中按 pageId 或页面名识别显式提及的页面。"""

    matched: list[str] = []
    for page in (product_plan or {}).get("pages", []):
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("pageId") or "").strip()
        name = str(page.get("name") or "").strip().lower()
        if not page_id:
            continue
        if re.search(rf"(?<![\w-]){re.escape(page_id.lower())}(?![\w-])", request) or (
            name and name in request
        ):
            matched.append(page_id)
    return matched
