from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.model_factory import create_chat_model
from app.config import Settings

RequestComplexity = Literal["simple", "complex"]


@dataclass(frozen=True)
class ComplexityDecision:
    complexity: RequestComplexity
    confidence: float
    reason: str
    signals: list[str]


CLASSIFIER_SYSTEM_PROMPT = (
    "Classify the user's request by semantic complexity, not by keyword matching. "
    "Return only a JSON object."
)

CLASSIFIER_USER_PROMPT_TEMPLATE = """Decide whether this user request should use the direct modification flow or the full planning workflow.

Definitions:
- simple: a localized edit, small fix, copy/style tweak, or narrow modification that can usually be handled directly.
- complex: a new app/project/system, multi-page or cross-layer feature, data/API/auth/workflow change, broad redesign, ambiguous product requirement, or anything that needs requirements confirmation and planning.

Favor "complex" when the request is unclear or could affect architecture, storage, APIs, permissions, multiple screens, build tasks, or acceptance criteria.

Return this JSON shape:
{{
  "complexity": "simple" | "complex",
  "confidence": number between 0 and 1,
  "reason": "short human-readable reason",
  "signals": ["brief semantic signals"]
}}

User request:
{request}
"""

_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


def _classifier_fallback(reason: str, signal: str) -> ComplexityDecision:
    return ComplexityDecision(
        complexity="complex",
        confidence=0.5,
        reason=reason,
        signals=[signal],
    )


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content or "")


def _json_from_text(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_PATTERN.search(text)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _coerce_decision(payload: dict[str, Any]) -> ComplexityDecision | None:
    complexity = payload.get("complexity")
    if complexity not in {"simple", "complex"}:
        return None

    try:
        confidence = float(payload.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = "Model classified the request complexity."

    raw_signals = payload.get("signals")
    signals = (
        [
            str(signal)
            for signal in raw_signals
            if isinstance(signal, (str, int, float)) and str(signal).strip()
        ]
        if isinstance(raw_signals, list)
        else []
    )
    if not signals:
        signals = ["model_semantic_classification"]

    return ComplexityDecision(
        complexity=complexity,
        confidence=confidence,
        reason=reason.strip(),
        signals=signals,
    )


def _invoke_model_classifier(request: str) -> ComplexityDecision:
    settings = Settings.from_env()
    model = create_chat_model(settings)
    result = model.invoke(
        [
            SystemMessage(content=CLASSIFIER_SYSTEM_PROMPT),
            HumanMessage(
                content=CLASSIFIER_USER_PROMPT_TEMPLATE.format(request=request.strip())
            ),
        ]
    )
    payload = _json_from_text(_message_text(getattr(result, "content", "")))
    decision = _coerce_decision(payload)
    if decision is None:
        return _classifier_fallback(
            "Model classifier returned an invalid response; defaulting to the full planning workflow.",
            "invalid_model_classifier_response",
        )
    return decision


def decide_request_complexity(request: str) -> ComplexityDecision:
    """Ask the configured chat model to route a request by semantic complexity."""

    if not request.strip():
        return _classifier_fallback(
            "Empty request; defaulting to the full planning workflow.",
            "empty_request",
        )
    try:
        return _invoke_model_classifier(request)
    except Exception as exc:
        return _classifier_fallback(
            f"Model classifier failed ({exc.__class__.__name__}); defaulting to the full planning workflow.",
            "model_classifier_error",
        )


def classify_request_complexity(request: str) -> RequestComplexity:
    return decide_request_complexity(request).complexity
