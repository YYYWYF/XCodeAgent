from __future__ import annotations

import re
from typing import Any, Literal

from app.agents.design_conversation.models import DesignConversationDecision


DesignConversationTarget = Literal[
    "requirements",
    "product_planning",
    "ui_confirmation",
]

_TARGET_BY_SEMANTICS: dict[tuple[str, str], DesignConversationTarget] = {
    ("requirement_change", "requirement"): "requirements",
    ("requirement_change", "product_behavior"): "product_planning",
    ("ui_change", "ui"): "ui_confirmation",
}

_PHASE_LABELS = {
    "planning": "计划",
    "development": "开发",
    "test": "测试",
}

_NATURAL_CONFIRMATION_PATTERNS = (
    re.compile(
        r"^(?:那)?(?:好|好的|可以|行|没问题)?(?:我)?(?:已|已经)?"
        r"确认(?:了)?(?:你)?(?:继续(?:规划)?(?:吧)?)?$"
    ),
    re.compile(r"^(?:那)?(?:好|好的|可以|行|没问题)(?:你)?继续(?:规划)?(?:吧)?$"),
)


def resolve_design_target(
    decision: DesignConversationDecision | Any,
    *,
    requirement_spec: dict[str, Any] | None,
    product_plan: dict[str, Any] | None,
) -> DesignConversationTarget | None:
    """只按允许的产品语义组合授予三个正式 Graph 节点的写权限。"""

    target = _TARGET_BY_SEMANTICS.get(
        (
            str(getattr(decision, "intent", "") or ""),
            str(getattr(decision, "change_level", "") or ""),
        )
    )
    suggested_phase = str(getattr(decision, "suggested_phase", "") or "")
    if target is None or suggested_phase not in {"", "none"}:
        return None
    if not _is_confirmed(requirement_spec):
        return "requirements"
    if target == "ui_confirmation" and not _is_confirmed(product_plan):
        return "product_planning"
    return target


def product_conversation_response(decision: DesignConversationDecision | Any) -> str:
    """为不进入正式产物节点的语义生成有界回复。"""

    intent = str(getattr(decision, "intent", "") or "")
    response = str(getattr(decision, "response", "") or "").strip()
    if intent in {"chat", "read_only"}:
        return response or "当前产品设计中没有足够信息确定这一点。"
    if intent == "clarification":
        question = str(getattr(decision, "clarification_question", "") or "").strip()
        return question or "请再说明你希望调整的产品事实、页面行为或 UI 表现。"
    if intent == "out_of_scope":
        if response:
            return response
        phase = str(getattr(decision, "suggested_phase", "") or "")
        phase_label = _PHASE_LABELS.get(phase)
        if phase_label:
            return (
                f"这个请求属于「{phase_label}」阶段，超出当前产品 Agent 的能力边界。"
                f"请切换到对应工程的{phase_label}阶段处理；"
                "当前应用的需求、产品行为和 UI 不会发生变化。"
            )
        return "这个请求超出当前产品阶段的能力范围，未修改任何正式产物。"
    return (
        "当前输入无法安全映射到产品阶段能力，未修改任何正式产物。"
        "请只描述产品需求、页面行为或 UI 期望。"
    )


def is_natural_language_confirmation(request: str) -> bool:
    """识别只表达“我确认了”的自由文本，禁止它冒充审阅卡结构化 action。"""

    normalized = re.sub(r"[\s,，。.!！?？;；:：]+", "", request.strip().lower())
    if not normalized:
        return False
    return any(pattern.fullmatch(normalized) for pattern in _NATURAL_CONFIRMATION_PATTERNS)


def _is_confirmed(artifact: dict[str, Any] | None) -> bool:
    """只把显式 confirmed 状态视为可越过的上游确认。"""

    return bool(artifact) and artifact.get("confirmation_status") == "confirmed"
