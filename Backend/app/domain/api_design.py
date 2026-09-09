"""Endpoint 字段映射的当前版领域模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


API_DESIGN_SCHEMA_VERSION = "endpoint-field-mapping.v3"
API_DESIGN_ARTIFACT_TYPE = "endpoint-field-mapping"


class ApiDesignModel(BaseModel):
    """为 API 字段映射模型提供严格字段和驼峰别名规则。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class EndpointFieldNode(ApiDesignModel):
    """描述工作台投影中的一个 Endpoint 输入或输出字段。"""

    node_type: Literal["endpoint_field"] = Field(default="endpoint_field", alias="nodeType")
    id: str = Field(min_length=1, max_length=1024)
    side: Literal["request", "response"]
    location: Literal["path", "query", "header", "request_body", "response_body"]
    path: str = Field(min_length=1, max_length=1024)
    type: str = Field(default="unknown", min_length=1, max_length=128)
    required: bool = False
    description: str = Field(default="", max_length=2048)


class EndpointField(ApiDesignModel):
    """描述正式字段映射内嵌的 Endpoint 字段快照。"""

    side: Literal["request", "response"]
    location: Literal["path", "query", "header", "request_body", "response_body"]
    path: str = Field(min_length=1, max_length=1024)
    type: str = Field(default="unknown", min_length=1, max_length=128)
    required: bool = False
    description: str = Field(default="", max_length=2048)


class DatabaseSourceField(ApiDesignModel):
    """描述字段映射内嵌的直属 MySQL 真实字段。"""

    source_type: Literal["database"] = Field(default="database", alias="sourceType")
    source_id: str = Field(alias="sourceId", min_length=1, max_length=128)
    schema_name: str = Field(alias="schema", min_length=1, max_length=256)
    table: str = Field(min_length=1, max_length=256)
    column: str = Field(min_length=1, max_length=256)
    type: str = Field(default="unknown", min_length=1, max_length=128)
    usage: Literal["read", "filter", "write"] = "read"
    description: str = Field(default="", max_length=2048)


class ExternalSourceField(ApiDesignModel):
    """描述字段映射内嵌的已保存外部 Operation 真实字段。"""

    source_type: Literal["external_api"] = Field(default="external_api", alias="sourceType")
    source_id: str = Field(alias="sourceId", min_length=1, max_length=128)
    directory_id: str = Field(alias="directoryId", min_length=1, max_length=128)
    operation_id: str = Field(alias="operationId", min_length=1, max_length=128)
    section: Literal["path", "query", "header", "request_body", "response_body"]
    path: str = Field(min_length=1, max_length=1024)
    type: str = Field(default="unknown", min_length=1, max_length=128)
    description: str = Field(default="", max_length=2048)


SourceField = Annotated[
    DatabaseSourceField | ExternalSourceField,
    Field(discriminator="source_type"),
]


class UnconfiguredFieldMapping(ApiDesignModel):
    """表示一个允许保持未配置的可选 Endpoint 字段。"""

    endpoint_field: EndpointField = Field(alias="endpointField")
    mapping_type: Literal["unconfigured"] = Field(default="unconfigured", alias="mappingType")


class BusinessDescriptionFieldMapping(ApiDesignModel):
    """表示一个仅由自然语言业务说明实现的 Endpoint 字段。"""

    endpoint_field: EndpointField = Field(alias="endpointField")
    mapping_type: Literal["business_description"] = Field(default="business_description", alias="mappingType")
    business_description: str = Field(alias="businessDescription", min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_description_shape(self) -> "BusinessDescriptionFieldMapping":
        """确保字段业务说明去除首尾空白后仍有实际内容。"""

        if not self.business_description.strip():
            raise ValueError("业务说明不能为空。")
        return self


class SourceMapping(ApiDesignModel):
    """表示 Endpoint 字段与真实来源列表及用户确认的处理说明。"""

    endpoint_field: EndpointField = Field(alias="endpointField")
    mapping_type: Literal["source_mapping"] = Field(default="source_mapping", alias="mappingType")
    source_fields: list[SourceField] = Field(alias="sourceFields", min_length=1, max_length=100)
    processing_type: Literal["direct", "single_field_description", "multi_field_description"] = Field(alias="processingType")
    business_description: str | None = Field(default=None, alias="businessDescription", max_length=2000)

    @model_validator(mode="after")
    def validate_processing(self) -> "SourceMapping":
        """校验来源数量与处理内容，直接映射不允许携带业务处理内容。"""
        if self.processing_type == "multi_field_description":
            if len(self.source_fields) < 2:
                raise ValueError("多字段业务处理至少需要两个来源。")
        elif len(self.source_fields) != 1:
            raise ValueError("直接映射或单字段业务处理必须只有一个来源。")
        if self.processing_type == "direct":
            if "business_description" in self.model_fields_set:
                raise ValueError("直接映射不能携带业务处理内容。")
        elif not self.business_description or not self.business_description.strip():
            raise ValueError("业务处理内容不能为空。")
        return self


ConfirmedFieldMapping = Annotated[
    SourceMapping | BusinessDescriptionFieldMapping,
    Field(discriminator="mapping_type"),
]


DraftFieldMapping = Annotated[
    UnconfiguredFieldMapping
    | BusinessDescriptionFieldMapping
    | SourceMapping,
    Field(discriminator="mapping_type"),
]


class SourceSnapshot(ApiDesignModel):
    """保存确认时使用的非敏感来源结构，供 Build 稳定消费。"""

    source_type: Literal["database", "external_api"] = Field(alias="sourceType")
    source_id: str = Field(alias="sourceId", min_length=1, max_length=128)
    name: str = Field(default="", max_length=256)
    details: dict[str, Any] = Field(default_factory=dict)


class ArtifactLineageReference(ApiDesignModel):
    """记录 API 设计直接依赖的正式 TechnicalPlan 哈希。"""

    artifact_key: Literal["technical-plan"] = Field(default="technical-plan", alias="artifactKey")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EndpointFieldMappingDesign(ApiDesignModel):
    """描述已确认的 Endpoint 自包含字段映射正式产物。"""

    schema_version: Literal["endpoint-field-mapping.v3"] = Field(default=API_DESIGN_SCHEMA_VERSION, alias="schemaVersion")
    artifact_type: Literal["endpoint-field-mapping"] = Field(default=API_DESIGN_ARTIFACT_TYPE, alias="artifactType")
    status: Literal["confirmed"] = "confirmed"
    confirmation_status: Literal["confirmed"] = Field(default="confirmed", alias="confirmationStatus")
    artifact_revision: str = Field(alias="artifactRevision", pattern=r"^[0-9a-f]{32}$")
    api_contract_id: str = Field(alias="apiContractId", min_length=1, max_length=256)
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=256)
    endpoint_contract: dict[str, Any] = Field(alias="endpointContract")
    implementation_description: str | None = Field(default=None, alias="implementationDescription", max_length=4000)
    field_mappings: list[ConfirmedFieldMapping] = Field(alias="fieldMappings", max_length=3000)
    source_snapshots: list[SourceSnapshot] = Field(default_factory=list, alias="sourceSnapshots", max_length=100)
    based_on: list[ArtifactLineageReference] = Field(alias="basedOn", min_length=1)
    confirmed_at: datetime = Field(alias="confirmedAt")


class ApiDesignAction(ApiDesignModel):
    """描述 API 设计卡片通过 AG-UI 提交的结构化动作。"""

    action: Literal["confirm"]
    api_contract_id: str = Field(alias="apiContractId", min_length=1, max_length=256)
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=256)
    draft: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_action_arguments(self) -> "ApiDesignAction":
        """按动作校验目标参数，阻止不完整请求进入节点。"""

        if self.action == "confirm" and not self.draft:
            raise ValueError("确认 API 设计必须提交当前完整草稿。")
        return self


class ApiDesignGateVersion(ApiDesignModel):
    """描述开发门禁确认时用户看到的一项 Endpoint 映射版本。"""

    api_contract_id: str = Field(alias="apiContractId", min_length=1, max_length=256)
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=256)
    artifact_revision: str = Field(alias="artifactRevision", pattern=r"^[0-9a-f]{32}$")


class ApiDesignGateAction(ApiDesignModel):
    """描述 API 开发门禁的刷新或版本确认动作。"""

    action: Literal["refresh", "confirm"]
    target_type: Literal["page", "endpoint"] = Field(alias="targetType")
    target_id: str = Field(alias="targetId", min_length=1, max_length=256)
    api_contract_id: str | None = Field(
        default=None,
        alias="apiContractId",
        min_length=1,
        max_length=256,
    )
    versions: list[ApiDesignGateVersion] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def validate_gate_arguments(self) -> "ApiDesignGateAction":
        """按目标和动作校验门禁参数，保证刷新与确认都绑定完整开发目标。"""

        if self.target_type == "endpoint" and not self.api_contract_id:
            raise ValueError("Endpoint 开发门禁必须携带 API Contract 标识。")
        if self.action == "confirm" and not self.versions:
            raise ValueError("确认 API 映射必须携带当前全部版本。")
        if self.action == "refresh" and self.versions:
            raise ValueError("重新检测 API 映射不能携带确认版本。")
        return self


class ApiDesignGateDesign(ApiDesignModel):
    """描述开发门禁回显的一项完整 Endpoint 映射。"""

    api_contract_id: str = Field(alias="apiContractId", min_length=1, max_length=256)
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=256)
    artifact_revision: str = Field(alias="artifactRevision", pattern=r"^[0-9a-f]{32}$")
    design: EndpointFieldMappingDesign


class ApiDesignGateResult(ApiDesignModel):
    """描述页面或接口开发门禁的聚合映射结果。"""

    status: Literal["ready", "confirmed"]
    target_type: Literal["page", "endpoint"] = Field(alias="targetType")
    target_id: str = Field(alias="targetId", min_length=1, max_length=256)
    target_label: str = Field(alias="targetLabel", min_length=1, max_length=512)
    designs: list[ApiDesignGateDesign] = Field(max_length=1000)
    confirmed_for_development: bool = Field(default=False, alias="confirmedForDevelopment")


EndpointApiDesign = EndpointFieldMappingDesign
