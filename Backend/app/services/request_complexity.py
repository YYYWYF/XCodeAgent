from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RequestComplexity = Literal["simple", "complex"]


@dataclass(frozen=True)
class ComplexityDecision:
    complexity: RequestComplexity
    confidence: float
    reason: str
    signals: list[str]


COMPLEX_INTENT_KEYWORDS = (
    "生成",
    "创建",
    "新建",
    "从零",
    "完整",
    "搭建",
    "实现一个",
    "做一个",
)

COMPLEX_SCOPE_KEYWORDS = (
    "应用",
    "工程",
    "项目",
    "系统",
    "后台",
    "管理端",
    "全栈",
    "前后端",
    "多页面",
    "多个页面",
    "数据库",
    "数据源",
    "数据模型",
    "接口",
    "api",
    "权限",
    "鉴权",
    "登录",
    "注册",
    "工作流",
    "支付",
    "审批",
    "部署",
)

SIMPLE_ACTION_KEYWORDS = (
    "修改",
    "调整",
    "修复",
    "优化",
    "改一下",
    "改成",
    "替换",
    "删除",
    "加一个",
    "加个",
    "增加",
    "隐藏",
    "显示",
)

SIMPLE_SCOPE_KEYWORDS = (
    "文案",
    "颜色",
    "样式",
    "按钮",
    "标题",
    "占位文案",
    "提示语",
    "间距",
    "字号",
    "图标",
    "图片",
    "链接",
    "路由文案",
    "loading",
    "empty",
    "error",
    "bug",
)

AMBIGUOUS_SEPARATORS = ("，", ",", "；", ";", "、", "\n")


def _matched_keywords(text: str, keywords: tuple[str, ...]) -> list[str]:
    return [keyword for keyword in keywords if keyword in text]


def _has_multiple_requirements(text: str) -> bool:
    return sum(text.count(separator) for separator in AMBIGUOUS_SEPARATORS) >= 2


def decide_request_complexity(request: str) -> ComplexityDecision:
    """Decide whether a request should use the simple or complex workflow.

    Classification rules:
    - New app/project/system generation is complex.
    - Requests touching API, data model, auth, permissions, workflow, payment,
      deployment, or multiple pages are complex.
    - Small local edits with an explicit local target are simple.
    - Ambiguous requests default to complex so the full requirement-confirmation
      workflow can protect the project.
    """

    normalized = request.strip().lower()
    if not normalized:
        return ComplexityDecision(
            complexity="complex",
            confidence=0.6,
            reason="Empty request; default to the full requirement-confirmation workflow.",
            signals=["empty_request"],
        )

    complex_intents = _matched_keywords(normalized, COMPLEX_INTENT_KEYWORDS)
    complex_scopes = _matched_keywords(normalized, COMPLEX_SCOPE_KEYWORDS)
    simple_actions = _matched_keywords(normalized, SIMPLE_ACTION_KEYWORDS)
    simple_scopes = _matched_keywords(normalized, SIMPLE_SCOPE_KEYWORDS)
    has_multiple_requirements = _has_multiple_requirements(normalized)

    if complex_intents and complex_scopes:
        return ComplexityDecision(
            complexity="complex",
            confidence=0.95,
            reason="Request looks like app/project-level generation.",
            signals=[
                *[f"complex_intent:{keyword}" for keyword in complex_intents],
                *[f"complex_scope:{keyword}" for keyword in complex_scopes],
            ],
        )

    if complex_scopes:
        return ComplexityDecision(
            complexity="complex",
            confidence=0.85,
            reason="Request touches architecture, data, API, auth, permissions, workflow, or deployment.",
            signals=[f"complex_scope:{keyword}" for keyword in complex_scopes],
        )

    if has_multiple_requirements:
        return ComplexityDecision(
            complexity="complex",
            confidence=0.75,
            reason="Request contains multiple requirement fragments; use full planning flow.",
            signals=["multiple_requirement_fragments"],
        )

    if simple_actions and simple_scopes:
        return ComplexityDecision(
            complexity="simple",
            confidence=0.9,
            reason="Request is a small local change with an explicit UI/content target.",
            signals=[
                *[f"simple_action:{keyword}" for keyword in simple_actions],
                *[f"simple_scope:{keyword}" for keyword in simple_scopes],
            ],
        )

    if simple_actions and len(normalized) <= 40:
        return ComplexityDecision(
            complexity="simple",
            confidence=0.7,
            reason="Request looks like a short local modification.",
            signals=[f"simple_action:{keyword}" for keyword in simple_actions],
        )

    return ComplexityDecision(
        complexity="complex",
        confidence=0.65,
        reason="Request is ambiguous; default to the full requirement-confirmation workflow.",
        signals=["ambiguous_default_complex"],
    )


def classify_request_complexity(request: str) -> RequestComplexity:
    return decide_request_complexity(request).complexity
