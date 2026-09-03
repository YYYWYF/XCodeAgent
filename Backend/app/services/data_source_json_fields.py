"""独立数据源 JSON 样例的路径、字段说明和字段类型校验。"""

from __future__ import annotations

import json
import math
from typing import Any, Literal, get_args

DataSourceFieldType = Literal["string", "integer", "number", "boolean", "object", "array", "null"]
FIELD_TYPES = frozenset(get_args(DataSourceFieldType))
PATH_FIELD_TYPES = frozenset({"string", "integer", "number", "boolean"})
MAX_FIELD_DESCRIPTIONS = 1000
MAX_FIELD_DESCRIPTION_LENGTH = 1024
MAX_FIELD_PATH_LENGTH = 4096
MAX_SAFE_INTEGER = 9007199254740991


class DataSourceJsonFieldError(ValueError):
    """表示 JSON 字段元数据不符合当前样例或类型契约。"""


def _json_field_paths(value: Any, path: str = "$", paths: set[str] | None = None) -> set[str]:
    """收集完整 JSON 样例中的字段路径，数组元素使用统一的 [] 路径。"""

    collected = paths if paths is not None else set()
    collected.add(path)
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f'{path}[{json.dumps(str(key), ensure_ascii=False)}]'
            _json_field_paths(child, child_path, collected)
    elif isinstance(value, list):
        if value:
            _json_field_paths(value[0], f"{path}[]", collected)
            for child in value[1:]:
                _json_field_paths(child, f"{path}[]", collected)
    return collected


def _normalize_field_descriptions(sample: Any, descriptions: Any) -> dict[str, str]:
    """按最终 JSON 样例清理说明，只保留仍然存在的字段路径。"""

    if sample is None or not descriptions:
        return {}
    if not isinstance(descriptions, dict):
        raise DataSourceJsonFieldError("JSON 字段说明必须是对象。")
    valid_paths = _json_field_paths(sample)
    normalized: dict[str, str] = {}
    for raw_path, raw_description in descriptions.items():
        if not isinstance(raw_path, str) or len(raw_path) > MAX_FIELD_PATH_LENGTH:
            raise DataSourceJsonFieldError("JSON 字段说明路径无效或过长。")
        if not isinstance(raw_description, str):
            raise DataSourceJsonFieldError("JSON 字段说明必须是文本。")
        description = raw_description.strip()
        if not description:
            continue
        if len(description) > MAX_FIELD_DESCRIPTION_LENGTH:
            raise DataSourceJsonFieldError("JSON 字段说明不能超过 1024 个字符。")
        if raw_path in valid_paths:
            normalized[raw_path] = description
    return normalized


def _validate_field_descriptions(sample: Any, descriptions: dict[str, str]) -> None:
    """校验已保存 JSON 字段说明，阻止说明映射引用不存在的路径。"""

    if sample is None:
        if descriptions:
            raise DataSourceJsonFieldError("未配置 JSON 样例时不能保存字段说明。")
        return
    if len(descriptions) > MAX_FIELD_DESCRIPTIONS:
        raise DataSourceJsonFieldError(f"JSON 字段说明不能超过 {MAX_FIELD_DESCRIPTIONS} 条。")
    valid_paths = _json_field_paths(sample)
    for raw_path in descriptions:
        if not isinstance(raw_path, str) or len(raw_path) > MAX_FIELD_PATH_LENGTH or raw_path not in valid_paths:
            raise DataSourceJsonFieldError("JSON 字段说明路径不存在于当前样例。")
    for description in descriptions.values():
        if not isinstance(description, str) or not description.strip():
            raise DataSourceJsonFieldError("JSON 字段说明不能为空。")
        if len(description.strip()) > MAX_FIELD_DESCRIPTION_LENGTH:
            raise DataSourceJsonFieldError("JSON 字段说明不能超过 1024 个字符。")


def _json_field_values(value: Any, path: str = "$", values: dict[str, list[Any]] | None = None) -> dict[str, list[Any]]:
    """收集完整样例中的共享路径值，数组中的全部匹配元素都参与类型校验。"""

    collected = values if values is not None else {}
    collected.setdefault(path, []).append(value)
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f'{path}[{json.dumps(str(key), ensure_ascii=False)}]'
            _json_field_values(child, child_path, collected)
    elif isinstance(value, list):
        for child in value:
            _json_field_values(child, f"{path}[]", collected)
    return collected


def matches_field_type(value: Any, field_type: str) -> bool:
    """判断 JSON 值是否符合字段声明，并排除 Python 中布尔值被当作数字的情况。"""

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


def normalize_field_types(sample: Any, field_types: Any) -> dict[str, str]:
    """先校验类型映射，再移除最终完整样例中缺失或不匹配的类型声明。"""

    if not isinstance(field_types, dict):
        raise DataSourceJsonFieldError("JSON 字段类型必须是对象。")
    if len(field_types) > MAX_FIELD_DESCRIPTIONS:
        raise DataSourceJsonFieldError("JSON 字段类型不能超过 1000 条。")
    for path, field_type in field_types.items():
        if not isinstance(path, str) or len(path) > MAX_FIELD_PATH_LENGTH:
            raise DataSourceJsonFieldError("JSON 字段类型路径无效或过长。")
        if not isinstance(field_type, str) or field_type not in FIELD_TYPES:
            raise DataSourceJsonFieldError("JSON 字段类型必须为 string、integer、number、boolean、object、array 或 null。")
    if sample is None:
        return {}
    values = _json_field_values(sample)
    return {
        path: field_type for path, field_type in field_types.items()
        if path in values and all(matches_field_type(value, field_type) for value in values[path])
    }


def normalize_operation_fields(operation: dict[str, Any]) -> dict[str, Any]:
    """规范化一个接口的请求和响应字段元数据，不改变 JSON 样例内容。"""

    result = dict(operation)
    for prefix in ("request", "response"):
        sample = operation.get(f"{prefix}Sample")
        result[f"{prefix}FieldDescriptions"] = _normalize_field_descriptions(
            sample, operation.get(f"{prefix}FieldDescriptions", {})
        )
        result[f"{prefix}FieldTypes"] = normalize_field_types(
            sample, operation.get(f"{prefix}FieldTypes", {})
        )
    return result


def validate_operation_fields(operation: dict[str, Any]) -> None:
    """校验完整接口中已保存的字段说明和类型，拒绝失效或不匹配的声明。"""

    for prefix in ("request", "response"):
        sample = operation.get(f"{prefix}Sample")
        _validate_field_descriptions(sample, operation.get(f"{prefix}FieldDescriptions", {}))
        field_types = operation.get(f"{prefix}FieldTypes", {})
        if normalize_field_types(sample, field_types) != field_types:
            raise DataSourceJsonFieldError("JSON 字段类型路径不存在于当前样例，或类型与样例不一致。")
