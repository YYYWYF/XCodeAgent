"""Endpoint 字段映射的当前版领域模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator
from app.domain.api_design_fields import ApiDesignModel, EndpointField
from app.domain.api_design_values import BusinessQueryRight, EndpointQueryRight, FixedQueryRight, QueryRight
from app.domain.api_design_database import DatabaseSourceField, DatabaseConditionOperator, DatabaseWriteMapping, DatabaseQueryCondition, DatabaseQuerySubgroup, DatabaseQuery


API_DESIGN_SCHEMA_VERSION = "endpoint-field-mapping.v7"
API_DESIGN_ARTIFACT_TYPE = "endpoint-field-mapping"


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


class ExternalApiBindingDraft(ApiDesignModel):
    """保存外部 API 请求目标及尚未选完的取值草稿。"""

    external_field: ExternalSourceField = Field(alias="externalField")
    right: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_request_target(self) -> "ExternalApiBindingDraft":
        """取值规则只能写入外部 Operation 的请求字段。"""

        if self.external_field.section == "response_body":
            raise ValueError("外部 API 取值目标必须是请求字段。")
        return self


class ExternalApiBinding(ApiDesignModel):
    """描述外部 API 请求目标使用的已确认取值规则。"""

    external_field: ExternalSourceField = Field(alias="externalField")
    right: QueryRight

    @model_validator(mode="after")
    def validate_request_value(self) -> "ExternalApiBinding":
        """拒绝把外部响应字段用作请求赋值目标。"""

        if self.external_field.section == "response_body":
            raise ValueError("外部 API 取值目标必须是请求字段。")
        return self


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
    endpoint_fields: list[EndpointField] = Field(default_factory=list, alias="endpointFields", max_length=100)
    builtin_fields: list[Literal["current_user_id", "current_time"]] = Field(default_factory=list, alias="builtinFields", max_length=2)
    processing_type: Literal["direct", "single_field_description", "multi_field_description"] = Field(alias="processingType")
    business_description: str | None = Field(default=None, alias="businessDescription", max_length=2000)
    missing_behavior: Literal["error", "omit", "default"] = Field(default="error", alias="missingBehavior")
    default_value: Any = Field(default=None, alias="defaultValue")

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
            if self.endpoint_fields or self.builtin_fields:
                raise ValueError("直接映射不能携带额外计算依赖。")
        elif not self.business_description or not self.business_description.strip():
            raise ValueError("业务处理内容不能为空。")
        if self.processing_type != "direct":
            BusinessQueryRight(origin="business", endpointFields=self.endpoint_fields, builtinFields=self.builtin_fields,
                               businessDescription=self.business_description, missingBehavior=self.missing_behavior, defaultValue=self.default_value)
        if self.missing_behavior == "default" and self.default_value is None:
            raise ValueError("请填写缺值时使用的默认值。")
        if self.missing_behavior != "default" and self.default_value is not None:
            raise ValueError("当前缺值策略不允许默认值。")
        return self


class ValueFieldMapping(ApiDesignModel):
    """描述返回值或纯业务入参的规则，字段依赖由统一右值模型表达。"""

    endpoint_field: EndpointField = Field(alias="endpointField")
    mapping_type: Literal["value_mapping"] = Field(default="value_mapping", alias="mappingType")
    right: QueryRight


ConfirmedFieldMapping = Annotated[
    SourceMapping | BusinessDescriptionFieldMapping | ValueFieldMapping,
    Field(discriminator="mapping_type"),
]


DraftFieldMapping = Annotated[
    UnconfiguredFieldMapping
    | BusinessDescriptionFieldMapping
    | ValueFieldMapping
    | SourceMapping,
    Field(discriminator="mapping_type"),
]


class SourceSnapshot(ApiDesignModel):
    """保存确认时使用的非敏感来源结构，供 Build 稳定消费。"""

    source_type: Literal["database", "external_api"] = Field(alias="sourceType")
    source_id: str = Field(alias="sourceId", min_length=1, max_length=128)
    name: str = Field(default="", max_length=256)
    details: dict[str, Any] = Field(default_factory=dict)


class DatabaseBinding(ApiDesignModel):
    """保存单表身份，即使返回字段全部为业务生成仍能恢复选择。"""
    source_type: Literal["database"] = Field(alias="sourceType")
    source_id: str = Field(alias="sourceId", min_length=1, max_length=128)
    schema_name: str = Field(alias="schema", min_length=1, max_length=256)
    table: str = Field(min_length=1, max_length=256)


class ExternalBinding(ApiDesignModel):
    """保存单一外部 Operation 的正式绑定身份。"""
    source_type: Literal["external_api"] = Field(alias="sourceType")
    source_id: str = Field(alias="sourceId", min_length=1, max_length=128)
    directory_id: str = Field(alias="directoryId", min_length=1, max_length=128)
    operation_id: str = Field(alias="operationId", min_length=1, max_length=128)


SourceBinding = Annotated[DatabaseBinding | ExternalBinding, Field(discriminator="source_type")]


class ArtifactLineageReference(ApiDesignModel):
    """记录 API 设计直接依赖的正式 TechnicalPlan 哈希。"""

    artifact_key: Literal["technical-plan"] = Field(default="technical-plan", alias="artifactKey")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EndpointFieldMappingDesign(ApiDesignModel):
    """描述已确认的 Endpoint 自包含字段映射正式产物。"""

    schema_version: Literal["endpoint-field-mapping.v7"] = Field(default=API_DESIGN_SCHEMA_VERSION, alias="schemaVersion")
    artifact_type: Literal["endpoint-field-mapping"] = Field(default=API_DESIGN_ARTIFACT_TYPE, alias="artifactType")
    status: Literal["confirmed"] = "confirmed"
    confirmation_status: Literal["confirmed"] = Field(default="confirmed", alias="confirmationStatus")
    artifact_revision: str = Field(alias="artifactRevision", pattern=r"^[0-9a-f]{32}$")
    api_contract_id: str = Field(alias="apiContractId", min_length=1, max_length=256)
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=256)
    endpoint_contract: dict[str, Any] = Field(alias="endpointContract")
    implementation_description: str | None = Field(default=None, alias="implementationDescription", max_length=4000)
    database_operation: Literal["create", "read", "update", "delete"] | None = Field(default=None, alias="databaseOperation")
    source_binding: SourceBinding | None = Field(default=None, alias="sourceBinding")
    field_mappings: list[ConfirmedFieldMapping] = Field(alias="fieldMappings", max_length=3000)
    database_writes: list[DatabaseWriteMapping] = Field(default_factory=list, alias="databaseWrites", max_length=3000)
    external_api_bindings: list[ExternalApiBinding] = Field(default_factory=list, alias="externalApiBindings", max_length=3000)
    database_query: DatabaseQuery | None = Field(default=None, alias="databaseQuery")
    source_snapshots: list[SourceSnapshot] = Field(default_factory=list, alias="sourceSnapshots", max_length=100)
    based_on: list[ArtifactLineageReference] = Field(alias="basedOn", min_length=1)
    confirmed_at: datetime = Field(alias="confirmedAt")

    @model_validator(mode="after")
    def validate_database_operation_shape(self) -> "EndpointFieldMappingDesign":
        """保证正式产物中的数据库来源与单一 CRUD 操作成对出现。"""

        has_database_source = any(
            isinstance(source, DatabaseSourceField)
            for mapping in self.field_mappings
            if isinstance(mapping, SourceMapping)
            for source in mapping.source_fields
        )
        has_conditions = self.database_query is not None
        has_writes = bool(self.database_writes)
        if has_database_source or has_conditions or has_writes or isinstance(self.source_binding, DatabaseBinding):
            if self.database_operation is None:
                raise ValueError("数据库正式映射必须包含 databaseOperation。")
            if self.database_operation == "create" and has_conditions:
                raise ValueError("新增正式映射不能包含 databaseQuery。")
            if self.database_operation in {"create", "update"} and not has_writes:
                raise ValueError("新增和修改正式映射必须包含 databaseWrites。")
            if self.database_operation == "update" and not has_conditions:
                raise ValueError("修改正式映射必须包含 databaseQuery。")
            if self.database_operation in {"read", "delete"} and has_writes:
                raise ValueError("查询和删除正式映射不能包含 databaseWrites。")
        elif self.database_operation is not None:
            raise ValueError("纯外部 API 正式映射不能包含 databaseOperation。")
        return self


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


class ApiDesignGateAction(ApiDesignModel):
    """描述 API 开发门禁的重新检测动作。"""

    action: Literal["refresh"]
    target_type: Literal["page", "endpoint"] = Field(alias="targetType")
    target_id: str = Field(alias="targetId", min_length=1, max_length=256)
    api_contract_id: str | None = Field(
        default=None,
        alias="apiContractId",
        min_length=1,
        max_length=256,
    )

    @model_validator(mode="after")
    def validate_gate_arguments(self) -> "ApiDesignGateAction":
        """校验重新检测动作绑定了完整开发目标。"""

        if self.target_type == "endpoint" and not self.api_contract_id:
            raise ValueError("Endpoint 开发门禁必须携带 API Contract 标识。")
        return self


EndpointApiDesign = EndpointFieldMappingDesign
