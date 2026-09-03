"""独立数据源请求与响应 JSON Schema 的生成、规范化和校验。"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer

DataSourceFieldType = Literal["string", "integer", "number", "boolean", "object", "array", "null"]
PATH_FIELD_TYPES = frozenset({"string", "integer", "number", "boolean"})
MAX_SAFE_INTEGER = 9007199254740991


class DataSourceJsonFieldError(ValueError):
    """表示 JSON 结构不符合当前样例或字段契约。"""


class JsonStructureNode(BaseModel):
    """使用 JSON Schema 标准关键词保存字段类型、说明、对象属性和数组元素。"""

    model_config = ConfigDict(extra="forbid")
    type: DataSourceFieldType | list[DataSourceFieldType]
    description: str | None = Field(default=None, max_length=1024)
    properties: dict[str, JsonStructureNode] | None = None
    items: JsonStructureNode | None = None

    @model_serializer(mode="wrap")
    def serialize_schema(self, handler: Any) -> dict[str, Any]:
        """省略未使用的标准关键词，避免输出 properties:null 或 items:null。"""
        return {key: value for key, value in handler(self).items() if value is not None}

    @field_validator("type")
    @classmethod
    def validate_types(cls, value: DataSourceFieldType | list[DataSourceFieldType]) -> DataSourceFieldType | list[DataSourceFieldType]:
        """联合类型列表必须非空且不重复，避免保存无效的标准 type 数组。"""
        if isinstance(value, list) and (not value or len(value) != len(set(value))):
            raise ValueError("JSON 结构的类型列表不能为空或重复。")
        return value


def matches_field_type(value: Any, field_type: str) -> bool:
    """判断 JSON 值是否符合声明，整数排除布尔值并限制在 JavaScript 安全范围内。"""

    if field_type == "null":
        return value is None
    if field_type == "boolean":
        return isinstance(value, bool)
    if field_type == "string":
        return isinstance(value, str)
    if field_type == "array":
        return isinstance(value, list)
    if field_type == "object":
        return isinstance(value, dict)
    if type(value) not in (int, float):
        return False
    if field_type == "number":
        return type(value) is int or math.isfinite(value)
    if field_type == "integer":
        return abs(value) <= MAX_SAFE_INTEGER and (type(value) is int or (math.isfinite(value) and value.is_integer()))
    return False


def build_json_structure(sample: Any, structure: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """依据完整样例重建标准结构，保留匹配类型和说明并清理已删除字段。"""

    declared = JsonStructureNode.model_validate(structure) if structure is not None else None
    if sample is None:
        return None

    def visit(values: list[Any], node: JsonStructureNode | None) -> dict[str, Any]:
        """合并所有数组元素的字段，不受前端预览深度、数量或折叠状态影响。"""
        inferred: list[str] = []
        for value in values:
            kind = next((kind for kind in ("null", "boolean", "string", "integer", "number", "object", "array") if matches_field_type(value, kind)), None)
            if kind is None:
                raise DataSourceJsonFieldError("JSON 样例包含不支持的字段值。")
            if kind not in inferred:
                inferred.append(kind)
        types = [node.type] if node and isinstance(node.type, str) else node.type if node else []
        # number 可以保留整数样例；声明不匹配时重新推导，但不修改样例值。
        if not types or not all(any(matches_field_type(value, kind) for kind in types) for value in values):
            types = inferred
        result: dict[str, Any] = {"type": types[0] if len(types) == 1 else types}
        if node and node.description and node.description.strip():
            result["description"] = node.description.strip()
        objects = [value for value in values if isinstance(value, dict)]
        if objects:
            grouped: dict[str, list[Any]] = {}
            for value in objects:
                for key, child in value.items():
                    grouped.setdefault(str(key), []).append(child)
            properties = node.properties if node and node.properties is not None else {}
            result["properties"] = {key: visit(children, properties.get(key)) for key, children in grouped.items()}
        items = [child for value in values if isinstance(value, list) for child in value]
        if items:
            result["items"] = visit(items, node.items if node else None)
        return result

    return visit([sample], declared)


def normalize_operation_fields(operation: dict[str, Any]) -> dict[str, Any]:
    """仅在保存时生成请求与响应结构，不维护独立的字段类型或说明映射。"""

    result = dict(operation)
    for prefix in ("request", "response"):
        result[f"{prefix}Structure"] = build_json_structure(operation.get(f"{prefix}Sample"), operation.get(f"{prefix}Structure"))
    return result


def validate_operation_fields(operation: dict[str, Any], *, stored: bool = False) -> None:
    """校验当前结构；已落盘结构不一致时报错，不在读取时迁移或重建文件。"""

    for prefix in ("request", "response"):
        structure = operation.get(f"{prefix}Structure")
        normalized = build_json_structure(operation.get(f"{prefix}Sample"), structure)
        if stored and structure != normalized:
            raise DataSourceJsonFieldError("已保存的 JSON 结构与样例、字段说明或类型不一致。")
