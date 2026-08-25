from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ApplicationPlanningArtifact = Literal[
    "requirement_spec",
    "requirement_document",
    "ui_designs",
    "technical_plan",
]
ApplicationPlanningAction = Literal[
    "answer",
    "confirm",
    "revise",
    "ui_action",
    "design_change",
]


class ApplicationPlanningInteraction(BaseModel):
    """校验前端提交给当前创建规划中断点的显式交互。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    gate_id: str = Field(alias="gateId", min_length=1, max_length=256)
    artifact: ApplicationPlanningArtifact
    artifact_revision: str = Field(
        alias="artifactRevision",
        min_length=1,
        max_length=64,
    )
    action: ApplicationPlanningAction
    request: str = Field(default="", max_length=20000)
    answers: dict[str, Any] = Field(default_factory=dict)
    edited_requirement_spec: dict[str, Any] | None = Field(
        default=None,
        alias="editedRequirementSpec",
    )
    requirement_spec_feedback: str = Field(
        default="",
        alias="requirementSpecFeedback",
        max_length=20000,
    )
    ui_action: dict[str, Any] | None = Field(default=None, alias="uiAction")

    @model_validator(mode="after")
    def validate_action_payload(self) -> "ApplicationPlanningInteraction":
        """校验动作与产物、文本及 UI 子动作之间的组合约束。"""

        if self.action in {"revise", "design_change"} and not self.request.strip():
            raise ValueError("修改动作必须提供明确的修改要求。")
        if self.action == "ui_action":
            if self.artifact != "ui_designs" or not isinstance(self.ui_action, dict):
                raise ValueError("UI 动作必须绑定 ui_designs 并提供 uiAction。")
        elif self.ui_action is not None:
            raise ValueError("只有 ui_action 可以携带 uiAction。")
        return self


def application_planning_artifact_revision(value: Any) -> str:
    """为待确认产物生成稳定摘要，用于拒绝旧卡片和重复提交。"""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def application_planning_gate_id(
    artifact: ApplicationPlanningArtifact,
    revision: str,
) -> str:
    """组合当前产物和版本摘要，形成前后端共同校验的审阅门标识。"""

    return f"{artifact}:{revision}"
