"""Endpoint 字段映射的当前版领域模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


API_DESIGN_SCHEMA_VERSION = "endpoint-field-mapping.v6"
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
    usage: Literal["read", "write"] = "read"
    description: str = Field(default="", max_length=2048)


DatabaseConditionOperator = Literal[
    "eq", "ne", "gt", "gte", "lt", "lte",
    "contains", "not_contains", "starts_with", "ends_with",
    "in", "not_in", "between", "not_between",
    "is_null", "is_not_null",
]


class EndpointQueryRight(ApiDesignModel):
    """引用当前 Endpoint 的请求参数作为查询右值。"""

    kind: Literal["endpoint"] = "endpoint"
    endpoint_field: EndpointField = Field(alias="endpointField")

    @model_validator(mode="after")
    def validate_request_side(self) -> "EndpointQueryRight":
        """拒绝把响应字段用作查询参数。"""

        if self.endpoint_field.side != "request":
            raise ValueError("数据库右值只能引用当前 Endpoint 的请求参数。")
        return self


class FixedQueryRight(ApiDesignModel):
    """保存查询条件的参数化固定值。"""

    kind: Literal["fixed"] = "fixed"
    value: Any


QueryRight = Annotated[EndpointQueryRight | FixedQueryRight, Field(discriminator="kind")]


class DatabaseWriteMapping(ApiDesignModel):
    """描述一个目标数据库列及其运行时写入值来源。"""

    source_type: Literal["database"] = Field(default="database", alias="sourceType")
    source_id: str = Field(alias="sourceId", min_length=1, max_length=128)
    schema_name: str = Field(alias="schema", min_length=1, max_length=256)
    table: str = Field(min_length=1, max_length=256)
    column: str = Field(min_length=1, max_length=256)
    type: str = Field(default="unknown", min_length=1, max_length=128)
    right: QueryRight
    description: str = Field(default="", max_length=2048)

    @model_validator(mode="after")
    def validate_value(self) -> "DatabaseWriteMapping":
        """确保写入字段已选择非空固定值或请求参数。"""

        if isinstance(self.right, FixedQueryRight) and self.right.value is None:
            raise ValueError("固定写入值不能为空。")
        return self


class DatabaseQueryCondition(ApiDesignModel):
    """描述用户手动添加的一条数据库查询条件。"""

    kind: Literal["condition"] = "condition"
    source_type: Literal["database"] = Field(default="database", alias="sourceType")
    source_id: str = Field(alias="sourceId", min_length=1, max_length=128)
    schema_name: str = Field(alias="schema", min_length=1, max_length=256)
    table: str = Field(min_length=1, max_length=256)
    column: str = Field(min_length=1, max_length=256)
    type: str = Field(default="unknown", min_length=1, max_length=128)
    operator: DatabaseConditionOperator
    right: QueryRight | None = None
    description: str = Field(default="", max_length=2048)

    @model_validator(mode="after")
    def validate_value_shape(self) -> "DatabaseQueryCondition":
        """约束运算符、右值来源以及固定值的形态和类型。"""

        if self.operator in {"is_null", "is_not_null"}:
            if self.right is not None:
                raise ValueError("空值查询条件不能携带右值。")
            return self
        if self.right is None:
            raise ValueError("查询条件必须选择接口参数或固定值。")
        if isinstance(self.right, EndpointQueryRight):
            return self
        value = self.right.value
        if value is None:
            raise ValueError("固定查询条件必须携带 value。")
        if self.operator in {"in", "not_in"} and (not isinstance(value, list) or not value):
            raise ValueError("IN/NOT IN 固定条件必须携带非空数组 value。")
        if self.operator in {"between", "not_between"} and (not isinstance(value, list) or len(value) != 2):
            raise ValueError("BETWEEN/NOT BETWEEN 固定条件必须携带两个元素的数组 value。")
        if self.operator not in {"in", "not_in", "between", "not_between"} and isinstance(value, list):
            raise ValueError("标量固定条件不能携带数组 value。")
        normalized = self.type.strip().lower().split("(", 1)[0]
        if any(token in normalized for token in ("int", "decimal", "numeric", "float", "double", "number")):
            family = "number"
        elif any(token in normalized for token in ("bool", "bit")):
            family = "boolean"
        elif any(token in normalized for token in ("timestamp", "datetime", "date", "time")):
            family = "temporal"
        elif any(token in normalized for token in ("char", "text", "string", "uuid", "enum")):
            family = "string"
        else:
            family = "unknown"
        allowed = {"eq", "ne"}
        if family in {"number", "temporal"}:
            allowed.update({"gt", "gte", "lt", "lte", "between", "not_between", "in", "not_in"})
        if family == "string":
            allowed.update({"contains", "not_contains", "starts_with", "ends_with", "in", "not_in"})
        if self.operator not in allowed:
            raise ValueError(f"列类型 {self.type} 不支持固定条件运算符 {self.operator}。")

        def scalar_valid(item: Any) -> bool:
            """判断固定标量是否与数据库列类型族一致。"""

            if family == "number":
                return isinstance(item, (int, float)) and not isinstance(item, bool)
            if family == "boolean":
                return isinstance(item, bool)
            if family in {"string", "temporal"}:
                return isinstance(item, str) and bool(item.strip())
            return item is not None and not isinstance(item, (list, dict))

        values = value if isinstance(value, list) else [value]
        if not all(scalar_valid(item) for item in values):
            raise ValueError(f"固定条件值与列类型 {self.type} 不兼容。")
        if self.operator in {"between", "not_between"} and values[0] > values[1]:
            raise ValueError("固定条件区间上下界倒置。")
        return self


class DatabaseQuerySubgroup(ApiDesignModel):
    """表达顶层查询中的一层括号分组。"""

    kind: Literal["group"] = "group"
    join: Literal["and", "or"] = "and"
    items: list[DatabaseQueryCondition] = Field(min_length=1, max_length=300)


DatabaseQueryItem = Annotated[
    DatabaseQueryCondition | DatabaseQuerySubgroup,
    Field(discriminator="kind"),
]


class DatabaseQuery(ApiDesignModel):
    """表达顶层 AND/OR 及最多一层子组的查询树。"""

    join: Literal["and", "or"] = "and"
    items: list[DatabaseQueryItem] = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def validate_size(self) -> "DatabaseQuery":
        """限制整棵查询树的叶子总数。"""

        count = sum(len(item.items) if isinstance(item, DatabaseQuerySubgroup) else 1 for item in self.items)
        if count > 300:
            raise ValueError("查询条件最多 300 条。")
        return self


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


class ExternalApiFixedValueDraft(ApiDesignModel):
    """保存外部 API 请求字段的固定值草稿，包括尚未填写的编辑态。"""

    external_field: ExternalSourceField = Field(alias="externalField")
    value: Any = None

    @model_validator(mode="after")
    def validate_request_target(self) -> "ExternalApiFixedValueDraft":
        """固定值只能写入外部 Operation 的请求字段。"""

        if self.external_field.section == "response_body":
            raise ValueError("外部 API 固定值目标必须是请求字段。")
        return self


class ExternalApiFixedValue(ApiDesignModel):
    """描述外部 API 请求参数使用的已确认固定值。"""

    external_field: ExternalSourceField = Field(alias="externalField")
    value: Any

    @model_validator(mode="after")
    def validate_request_value(self) -> "ExternalApiFixedValue":
        """拒绝响应字段目标和空白固定值。"""

        if self.external_field.section == "response_body":
            raise ValueError("外部 API 固定值目标必须是请求字段。")
        if self.value is None:
            raise ValueError("外部 API 固定值不能为空。")
        if isinstance(self.value, str) and not self.value.strip():
            raise ValueError("外部 API 固定值不能为空白。")
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

    schema_version: Literal["endpoint-field-mapping.v6"] = Field(default=API_DESIGN_SCHEMA_VERSION, alias="schemaVersion")
    artifact_type: Literal["endpoint-field-mapping"] = Field(default=API_DESIGN_ARTIFACT_TYPE, alias="artifactType")
    status: Literal["confirmed"] = "confirmed"
    confirmation_status: Literal["confirmed"] = Field(default="confirmed", alias="confirmationStatus")
    artifact_revision: str = Field(alias="artifactRevision", pattern=r"^[0-9a-f]{32}$")
    api_contract_id: str = Field(alias="apiContractId", min_length=1, max_length=256)
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=256)
    endpoint_contract: dict[str, Any] = Field(alias="endpointContract")
    implementation_description: str | None = Field(default=None, alias="implementationDescription", max_length=4000)
    database_operation: Literal["create", "read", "update", "delete"] | None = Field(default=None, alias="databaseOperation")
    field_mappings: list[ConfirmedFieldMapping] = Field(alias="fieldMappings", max_length=3000)
    database_writes: list[DatabaseWriteMapping] = Field(default_factory=list, alias="databaseWrites", max_length=3000)
    external_api_fixed_values: list[ExternalApiFixedValue] = Field(default_factory=list, alias="externalApiFixedValues", max_length=3000)
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
        if has_database_source or has_conditions or has_writes:
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
