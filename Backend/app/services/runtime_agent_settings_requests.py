"""Agent 设置编辑仅接收 Prompt／Temperature 及服务端签发的草稿身份。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.services.runtime_agent_editable_config import SettingsPatch


class SettingsRequest(BaseModel):
    """约束当前工作区和 Agent 身份，拒绝额外配置写入。"""

    model_config = ConfigDict(extra="forbid")
    workspaceRoot: str = Field(min_length=1, max_length=4096)
    agentId: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=128)


class GetSettingsRequest(SettingsRequest):
    """读取当前配置身份及尚未确认的草稿。"""

    action: Literal["get_settings"]


class PreviewSettingsRequest(SettingsRequest):
    """提交 Prompt／Temperature 的精确变化段，并绑定当前技术规划。"""

    action: Literal["preview_settings"]
    contractHash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    technicalPlanSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    settingsPatch: SettingsPatch


class SubmitSettingsRequest(SettingsRequest):
    """只确认或放弃服务端已保存的预览，不能附带另一份配置。"""

    action: Literal["confirm_settings", "cancel_settings"]
    changeId: str = Field(pattern=r"^chg_[0-9a-f]{32}$")
    draftSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lifecycleRevision: int = Field(ge=1)


SETTINGS_ACTIONS = {"get_settings", "preview_settings", "confirm_settings", "cancel_settings"}
settings_request_adapter = TypeAdapter(Annotated[
    GetSettingsRequest | PreviewSettingsRequest | SubmitSettingsRequest, Field(discriminator="action")
])
