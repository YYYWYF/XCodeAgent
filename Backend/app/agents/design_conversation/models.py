from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ProductConversationIntent = Literal[
    "chat",
    "read_only",
    "requirement_change",
    "ui_change",
    "clarification",
    "out_of_scope",
]
ProductChangeLevel = Literal[
    "requirement",
    "product_behavior",
    "ui",
    "none",
]
SuggestedPhase = Literal[
    "planning",
    "development",
    "test",
    "none",
]


class DesignConversationDecision(BaseModel):
    """产品阶段对话 Coordinator 的稳定语义结果。"""

    model_config = ConfigDict(extra="forbid")

    intent: ProductConversationIntent
    change_level: ProductChangeLevel = "none"
    reason: str = Field(min_length=1, max_length=500)
    affected_page_ids: list[str] = Field(default_factory=list, max_length=100)
    response: str = Field(default="", max_length=4000)
    suggested_phase: SuggestedPhase = "none"
    clarification_question: str = Field(default="", max_length=2000)
