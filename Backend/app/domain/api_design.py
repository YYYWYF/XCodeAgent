"""Endpoint 字段映射的当前版领域模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


API_DESIGN_SCHEMA_VERSION = "endpoint-field-mapping.v1"
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


class SceneEntityField(ApiDesignModel):
    """定义当前 Endpoint 场景 Entity 中的一个局部字段。"""

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    label: str = Field(default="", max_length=256)
    type: str = Field(default="unknown", min_length=1, max_length=128)
    required: bool = False
    description: str = Field(default="", max_length=2048)


class SceneEntity(ApiDesignModel):
    """描述当前 Endpoint 复制得到的只读 TechnicalPlan 场景实体。"""

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2048)
    template_entity_id: str = Field(alias="templateEntityId", min_length=1, max_length=256)
    fields: list[SceneEntityField] = Field(default_factory=list, max_length=500)


class EntityTemplateField(ApiDesignModel):
    """为当前 Endpoint 提供复制入口的 TechnicalPlan 实体字段模板。"""

    name: str = Field(min_length=1, max_length=256)
    label: str = Field(default="", max_length=256)
    type: str = Field(default="unknown", min_length=1, max_length=128)
    required: bool = False
    description: str = Field(default="", max_length=2048)


class EntityTemplate(ApiDesignModel):
    """只读的全局实体复制模板，不承载任何数据源绑定。"""

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2048)
    fields: list[EntityTemplateField] = Field(default_factory=list, max_length=500)


class EntityFieldReference(ApiDesignModel):
    """内嵌引用当前场景实体中的一个字段。"""

    entity_id: str = Field(alias="entityId", min_length=1, max_length=256)
    field_id: str = Field(alias="fieldId", min_length=1, max_length=256)
    path: str = Field(min_length=1, max_length=1024)
    type: str = Field(default="unknown", min_length=1, max_length=128)


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


class DirectSourceFieldMapping(ApiDesignModel):
    """表示 Endpoint 字段与一个真实数据源字段直接对应。"""

    endpoint_field: EndpointField = Field(alias="endpointField")
    mapping_type: Literal["direct_source"] = Field(default="direct_source", alias="mappingType")
    source_field: SourceField = Field(alias="sourceField")


class ThroughEntityFieldMapping(ApiDesignModel):
    """表示 Endpoint 字段经场景实体字段映射，并可继续连接数据源。"""

    endpoint_field: EndpointField = Field(alias="endpointField")
    mapping_type: Literal["through_entity"] = Field(default="through_entity", alias="mappingType")
    entity_field: EntityFieldReference = Field(alias="entityField")
    source_field: SourceField | None = Field(default=None, alias="sourceField")


FieldMapping = Annotated[
    UnconfiguredFieldMapping
    | BusinessDescriptionFieldMapping
    | DirectSourceFieldMapping
    | ThroughEntityFieldMapping,
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

    schema_version: Literal["endpoint-field-mapping.v1"] = Field(default=API_DESIGN_SCHEMA_VERSION, alias="schemaVersion")
    artifact_type: Literal["endpoint-field-mapping"] = Field(default=API_DESIGN_ARTIFACT_TYPE, alias="artifactType")
    status: Literal["confirmed"] = "confirmed"
    confirmation_status: Literal["confirmed"] = Field(default="confirmed", alias="confirmationStatus")
    artifact_revision: str = Field(alias="artifactRevision", pattern=r"^[0-9a-f]{32}$")
    api_contract_id: str = Field(alias="apiContractId", min_length=1, max_length=256)
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=256)
    endpoint_contract: dict[str, Any] = Field(alias="endpointContract")
    implementation_description: str | None = Field(default=None, alias="implementationDescription", max_length=4000)
    scene_entities: list[SceneEntity] = Field(default_factory=list, alias="sceneEntities", max_length=200)
    field_mappings: list[FieldMapping] = Field(alias="fieldMappings", max_length=3000)
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


EndpointApiDesign = EndpointFieldMappingDesign
