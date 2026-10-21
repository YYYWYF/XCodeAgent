"""单表查询树与写入目标的字段映射模型。"""

from typing import Annotated, Any, Literal
from pydantic import Field, model_validator
from app.domain.api_design_fields import ApiDesignModel
from app.domain.api_design_values import BusinessQueryRight, EndpointQueryRight, FixedQueryRight, QueryRight


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
        if isinstance(self.right, (EndpointQueryRight, BusinessQueryRight)):
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


