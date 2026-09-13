"""应用配置变更提案的当前契约；此模型本身不授权或执行文件写入。"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


ApplicationConfigPath = Literal[
    "auth.enable",
    "authorization.enabled",
    "track.enable",
    "apiTrack.enable",
]

# 从同一类型定义导出白名单，供后续 Resolver 和 Mutation Service 复用。
NATURAL_LANGUAGE_MUTABLE_APPLICATION_CONFIG: frozenset[str] = frozenset(
    get_args(ApplicationConfigPath)
)


class ApplicationConfigChange(BaseModel):
    """描述平台依据当前应用配置构造的一项待确认布尔配置变更。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    path: ApplicationConfigPath
    operation: Literal["set"]
    from_value: StrictBool = Field(alias="from")
    to_value: StrictBool = Field(alias="to")
    reason: str = Field(strict=True, min_length=1)
    evidence: str = Field(strict=True, min_length=1)

    @field_validator("reason", "evidence")
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        """拒绝空白说明，同时保留用户原始证据文本用于审计。"""

        if not value.strip():
            raise ValueError("配置变更的 reason 和 evidence 不得为空白。")
        return value

    @model_validator(mode="after")
    def validate_changed_value(self) -> "ApplicationConfigChange":
        """拒绝无实际变化的提案，已满足的配置目标不应进入待提交集合。"""

        if self.from_value == self.to_value:
            raise ValueError("配置变更的 from 与 to 必须不同。")
        return self
