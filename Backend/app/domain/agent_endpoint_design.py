"""运行接口的 v8 接入设计，只允许审阅固定能力，不承载实体字段映射。"""
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class AgentEndpointDraft(BaseModel):
    """正式与草稿共用的 Agent 分支字段，禁止混入 CRUD 配置。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    api_contract_id: str = Field(alias="apiContractId", min_length=1, max_length=256)
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=256)
    implementation_kind: Literal["agent_runtime"] = Field(alias="implementationKind")
    source_binding: dict[str, str] = Field(alias="sourceBinding")
    input_bindings: list[Any] = Field(alias="inputBindings", max_length=0)
    invocation: dict[str, Any]
    interaction_contract: dict[str, Any] = Field(alias="interactionContract")
    capability_sha256: str = Field(alias="capabilitySha256", pattern=r"^sha256:[0-9a-f]{64}$")


class AgentEndpointDesign(AgentEndpointDraft):
    """确认仅绑定当前候选版本及 Markdown 字节，不能靠旧 confirmed 绕过提交。"""

    schema_version: Literal["endpoint-design.v8"] = Field(default="endpoint-design.v8", alias="schemaVersion")
    artifact_type: Literal["endpoint-design"] = Field(default="endpoint-design", alias="artifactType")
    status: Literal["confirmed"] = "confirmed"
    confirmation_status: Literal["confirmed"] = Field(default="confirmed", alias="confirmationStatus")
    artifact_revision: str = Field(alias="artifactRevision", pattern=r"^[0-9a-f]{32}$")
    markdown_sha256: str | None = Field(default=None, alias="markdownSha256", pattern=r"^[0-9a-f]{64}$")
    endpoint_contract: dict[str, Any] = Field(alias="endpointContract")
    based_on: list[dict[str, str]] = Field(alias="basedOn", min_length=1)
    confirmed_at: datetime = Field(alias="confirmedAt")
