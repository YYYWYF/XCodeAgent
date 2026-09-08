"""Agent Settings 正式修订使用的严格请求模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentSettingsRevisionModel(BaseModel):
    """为 Agent Settings 修订请求提供严格驼峰合同。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class AgentPersonaPatch(AgentSettingsRevisionModel):
    """限制人设表单可提交的角色与语气。"""

    role: str = Field(min_length=1, max_length=2_000)
    tone: str = Field(min_length=1, max_length=2_000)


class AgentPromptPatch(AgentSettingsRevisionModel):
    """限制第一批可编辑的完整业务 Prompt 字段。"""

    persona: AgentPersonaPatch
    system_prompt: str = Field(alias="systemPrompt", min_length=1, max_length=100_000)
    constraints: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_constraints(self) -> "AgentPromptPatch":
        """拒绝空白约束和过长单项，避免不可见配置进入正式 Contract。"""

        if any(not item.strip() for item in self.constraints):
            raise ValueError("Prompt 约束不能包含空白项。")
        if any(len(item) > 4_000 for item in self.constraints):
            raise ValueError("单条 Prompt 约束不能超过 4000 个字符。")
        return self


class AgentGenerationPatch(AgentSettingsRevisionModel):
    """限制当前可编辑的模型生成参数。"""

    temperature: float = Field(ge=0, le=2)


class AgentModelPatch(AgentSettingsRevisionModel):
    """只允许修改当前 Runtime 已支持的 Temperature。"""

    generation: AgentGenerationPatch


class AgentSettingsPatch(AgentSettingsRevisionModel):
    """描述第一批允许写入的 Agent Settings 分段。"""

    prompt: AgentPromptPatch | None = None
    model: AgentModelPatch | None = None


class PrepareAgentSettingsRevisionRequest(AgentSettingsRevisionModel):
    """校验生成 Agent Settings 正式修改预览所需输入。"""

    action: Literal["prepare_agent_settings_revision"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    agent_id: str = Field(alias="agentId", min_length=1, max_length=512)
    based_on_contract_hash: str = Field(
        alias="basedOnContractHash", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    based_on_technical_plan_sha256: str = Field(
        alias="basedOnTechnicalPlanSha256", pattern=r"^sha256:[0-9a-f]{64}$"
    )
    changed_sections: list[Literal["prompt", "model"]] = Field(
        alias="changedSections", min_length=1, max_length=2
    )
    settings_patch: AgentSettingsPatch = Field(alias="settingsPatch")

    @model_validator(mode="after")
    def validate_changed_sections(self) -> "PrepareAgentSettingsRevisionRequest":
        """要求 changedSections 与实际 Patch 精确一致且不存在重复段。"""

        actual = {
            key
            for key in ("prompt", "model")
            if getattr(self.settings_patch, key) is not None
        }
        declared = set(self.changed_sections)
        if len(declared) != len(self.changed_sections) or declared != actual:
            raise ValueError("changedSections 必须与 settingsPatch 精确一致且不得重复。")
        return self


class ConfirmAgentSettingsRevisionRequest(AgentSettingsRevisionModel):
    """校验确认应用 Agent Settings 修改所需的 CAS 身份。"""

    action: Literal["confirm_agent_settings_revision"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    agent_id: str = Field(alias="agentId", min_length=1, max_length=512)
    change_id: str = Field(alias="changeId", min_length=1, max_length=256)
    based_on_lifecycle_revision: int = Field(alias="basedOnLifecycleRevision", ge=1)
    draft_sha256: str = Field(alias="draftSha256", pattern=r"^[0-9a-f]{64}$")


class AbandonAgentSettingsRevisionRequest(AgentSettingsRevisionModel):
    """校验放弃当前 Agent Settings revision draft 的身份。"""

    action: Literal["abandon_agent_settings_revision"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    agent_id: str = Field(alias="agentId", min_length=1, max_length=512)
    change_id: str = Field(alias="changeId", min_length=1, max_length=256)
    based_on_lifecycle_revision: int = Field(alias="basedOnLifecycleRevision", ge=1)
    draft_sha256: str = Field(alias="draftSha256", pattern=r"^[0-9a-f]{64}$")


class GetAgentSettingsRevisionRequest(AgentSettingsRevisionModel):
    """校验恢复当前 Agent Settings 修改预览所需的工作区与 Agent。"""

    action: Literal["get_agent_settings_revision"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    agent_id: str = Field(alias="agentId", min_length=1, max_length=512)


AgentSettingsRevisionRequest = (
    PrepareAgentSettingsRevisionRequest
    | ConfirmAgentSettingsRevisionRequest
    | AbandonAgentSettingsRevisionRequest
    | GetAgentSettingsRevisionRequest
)


def parse_agent_settings_revision_request(value: dict[str, Any]) -> AgentSettingsRevisionRequest:
    """按 action 选择严格请求模型，拒绝未知或旧版动作。"""

    action = str(value.get("action") or "").strip()
    models = {
        "prepare_agent_settings_revision": PrepareAgentSettingsRevisionRequest,
        "confirm_agent_settings_revision": ConfirmAgentSettingsRevisionRequest,
        "abandon_agent_settings_revision": AbandonAgentSettingsRevisionRequest,
        "get_agent_settings_revision": GetAgentSettingsRevisionRequest,
    }
    model = models.get(action)
    if model is None:
        raise ValueError("不支持的 Agent Settings revision action。")
    return model.model_validate(value)
