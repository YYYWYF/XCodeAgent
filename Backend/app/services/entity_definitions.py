from __future__ import annotations

import json
import re
from typing import Any


ENTITY_FIELD_TYPES = frozenset(
    {
        "text",
        "long_text",
        "number",
        "decimal",
        "date",
        "datetime",
        "enum",
        "boolean",
    }
)

ENTITY_FIELD_TYPE_LABELS = {
    "text": "文本",
    "long_text": "长文本",
    "number": "数字",
    "decimal": "小数",
    "date": "日期",
    "datetime": "日期时间",
    "enum": "枚举",
    "boolean": "布尔",
}

ENTITY_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

RESERVED_ENTITY_FIELD = "id"

# 业务语义类型到 MySQL 列类型的固定映射，属于内部确定性逻辑，不写入用户可见工件。
MYSQL_TYPE_BY_FIELD_TYPE = {
    "text": "VARCHAR(255)",
    "long_text": "TEXT",
    "number": "BIGINT",
    "decimal": "DECIMAL(12,2)",
    "date": "DATE",
    "datetime": "DATETIME",
    "enum": "VARCHAR(32)",
    "boolean": "TINYINT(1)",
}

JSON_TYPE_BY_FIELD_TYPE = {
    "text": "string",
    "long_text": "string",
    "number": "integer",
    "decimal": "number",
    "date": "string",
    "datetime": "string",
    "enum": "string",
    "boolean": "boolean",
}

JSON_FORMAT_BY_FIELD_TYPE = {
    "date": "date",
    "datetime": "date-time",
}


def entity_field_type_label(field_type: str) -> str:
    """把语义字段类型转换为稳定的中文展示标签。"""

    return ENTITY_FIELD_TYPE_LABELS.get(field_type, str(field_type or "文本"))


def _derived_field_name(label: str, index: int) -> str:
    """从字段展示名称派生稳定的 snake_case 字段名，中文等非 ASCII 用序号兜底。"""

    ascii_slug = re.sub(r"[^a-z0-9]+", "_", str(label or "").lower()).strip("_")
    return ascii_slug or f"field_{index + 1}"


def entity_table_name(entity_id: str) -> str:
    """把实体 id 转换为 snake_case 表名，作为数据库目标表的规范名称。"""

    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", str(entity_id or "")).lower()
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized).strip("_")
    return normalized or "entity"


def entity_ids(entities: Any) -> list[str]:
    """提取实体 id 列表，兼容旧版字符串实体。"""

    result: list[str] = []
    for item in entities if isinstance(entities, list) else []:
        if isinstance(item, dict):
            entity_id = str(item.get("id") or "").strip()
        elif isinstance(item, str):
            entity_id = item.strip()
        else:
            entity_id = ""
        if entity_id and entity_id not in result:
            result.append(entity_id)
    return result


def normalize_data_source_type(value: Any) -> str:
    """把实体数据源绑定归一为三种正式类型之一，缺省默认 database。"""

    if isinstance(value, str):
        candidate = value.strip()
    elif isinstance(value, dict):
        candidate = str(value.get("type") or value.get("id") or "").strip()
    else:
        candidate = ""
    return candidate if candidate in {"database", "external_api", "static"} else "database"


def data_source_type_label(source_type: str) -> str:
    """把数据源类型转换为稳定中文标签。"""

    return {
        "database": "数据库",
        "external_api": "外部 API",
        "static": "静态数据",
    }.get(source_type, source_type or "数据库")


def plan_data_sources(project_plan: Any) -> list[dict[str, Any]]:
    """从已确认实体设计推导数据源列表。

    计划阶段实体不再生成 data_source，数据源在实体设计阶段选择并确认；
    尚未设计的实体不出现在数据源清单中，契约数据源由已确认实体设计反查。
    """

    entity_to_type: dict[str, str] = {}
    entities: list[dict[str, Any]] = []
    if isinstance(project_plan, dict):
        for detail in project_plan.get("entity_detail_plans") or []:
            if not isinstance(detail, dict):
                continue
            entity_id = str(detail.get("entity_id") or "").strip()
            if not entity_id:
                continue
            if str(detail.get("status") or "") != "confirmed":
                continue
            source_type = normalize_data_source_type(
                detail.get("data_source_type") or detail.get("data_source_id")
            )
            if source_type:
                entity_to_type.setdefault(entity_id, source_type)
        for entity in project_plan.get("entities") or []:
            if isinstance(entity, dict):
                entities.append(entity)

    sources: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entity in entities:
        entity_id = str(entity.get("id") or "").strip()
        source_type = entity_to_type.get(entity_id)
        if not source_type:
            continue
        if source_type not in sources:
            sources[source_type] = {
                "id": source_type,
                "name": data_source_type_label(source_type),
                "type": source_type,
                "entities": [],
                "schema_refs": [],
                "seed_strategy": "demo_records",
            }
            order.append(source_type)
        sources[source_type]["entities"].append(entity)
    return [sources[source_type] for source_type in order]


def contract_data_source_id(project_plan: Any, contract: Any) -> str:
    """按契约 entity_ids 反查实体数据源类型，契约不再携带 data_source_id。"""

    if not isinstance(contract, dict) or not isinstance(project_plan, dict):
        return ""
    entity_to_source: dict[str, str] = {}
    for source in plan_data_sources(project_plan):
        source_id = str(source.get("id") or "")
        for entity in source.get("entities") if isinstance(source.get("entities"), list) else []:
            if not isinstance(entity, dict):
                continue
            entity_id = str(entity.get("id") or "")
            if entity_id and entity_id not in entity_to_source:
                entity_to_source[entity_id] = source_id
    for entity_id in contract.get("entity_ids") if isinstance(contract.get("entity_ids"), list) else []:
        resolved = entity_to_source.get(str(entity_id or ""))
        if resolved:
            return resolved
    return ""


def confirmed_entity_designs(project_plan: Any, contract: Any) -> list[dict[str, Any]]:
    """按契约 entity_ids 顺序返回已确认实体设计，作为接口构建上下文的实体事实来源。"""

    if not isinstance(contract, dict) or not isinstance(project_plan, dict):
        return []
    entity_ids = [
        str(item).strip()
        for item in contract.get("entity_ids") if isinstance(contract.get("entity_ids"), list)
        if str(item).strip()
    ]
    confirmed_details = {
        str(detail.get("entity_id") or ""): detail
        for detail in project_plan.get("entity_detail_plans") or []
        if isinstance(detail, dict)
        and str(detail.get("status") or "") == "confirmed"
        and str(detail.get("entity_id") or "")
    }
    return [
        confirmed_details[entity_id]
        for entity_id in entity_ids
        if entity_id in confirmed_details
    ]


def missing_entity_design_ids(project_plan: Any, contract: Any) -> list[str]:
    """返回契约绑定但尚无已确认实体设计的实体 id 清单，供构建门禁给出可定位错误。"""

    if not isinstance(contract, dict) or not isinstance(project_plan, dict):
        return []
    entity_ids = [
        str(item).strip()
        for item in contract.get("entity_ids") if isinstance(contract.get("entity_ids"), list)
        if str(item).strip()
    ]
    confirmed_ids = {
        str(detail.get("entity_id") or "")
        for detail in confirmed_entity_designs(project_plan, contract)
    }
    return [entity_id for entity_id in entity_ids if entity_id not in confirmed_ids]


def entity_design_source_type(detail: Any) -> str:
    """归一化读取实体设计的数据源类型；确认设计缺失类型时按默认 database 处理。"""

    if not isinstance(detail, dict):
        return ""
    return normalize_data_source_type(
        detail.get("data_source_type") or detail.get("data_source_id")
    )


def entity_design_summaries(
    project_plan: Any,
    entity_ids: Any,
    endpoint_refs: set[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """把已确认实体设计压缩为有界摘要，并按当前 Endpoint 裁剪上游操作。"""

    requested_ids = [
        str(item).strip()
        for item in entity_ids if isinstance(entity_ids, list)
        if str(item).strip()
    ]
    confirmed_details = {
        str(detail.get("entity_id") or ""): detail
        for detail in project_plan.get("entity_detail_plans") or []
        if isinstance(detail, dict)
        and str(detail.get("status") or "") == "confirmed"
        and str(detail.get("entity_id") or "")
    }
    return [
        _entity_design_summary(confirmed_details[entity_id], endpoint_refs)
        for entity_id in requested_ids
        if entity_id in confirmed_details
    ]


def _entity_design_summary(
    detail: dict[str, Any],
    endpoint_refs: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """把单个已确认实体设计压缩为任务规划可读的有界摘要。"""

    fields = detail.get("fields") if isinstance(detail.get("fields"), list) else []
    summary: dict[str, Any] = {
        "entity_id": str(detail.get("entity_id") or ""),
        "entity_name": str(detail.get("entity_name") or detail.get("entity_id") or ""),
        "data_source_type": entity_design_source_type(detail),
        "fields": [
            {
                "name": str(field.get("name") or ""),
                "label": str(field.get("label") or field.get("name") or ""),
                "type": str(field.get("type") or "text"),
                "required": bool(field.get("required")),
                "description": str(field.get("description") or "")[:500],
                "enum_values": [
                    str(item)[:200]
                    for item in field.get("enum_values") or []
                    if isinstance(item, str) and str(item).strip()
                ][:100],
            }
            for field in fields[:100]
            if isinstance(field, dict) and field.get("name")
        ],
    }
    database_design = (
        detail.get("database_design")
        if isinstance(detail.get("database_design"), dict)
        else {}
    )
    if database_design:
        bindings = _design_items(database_design.get("bindings"))
        table_generation = (
            database_design.get("table_generation")
            if isinstance(database_design.get("table_generation"), dict)
            else {}
        )
        database_execution = (
            detail.get("database_execution")
            if isinstance(detail.get("database_execution"), dict)
            else {}
        )
        summary["database_design"] = {
            "database_name": str(database_design.get("database_name") or ""),
            "matched_table": database_design.get("matched_table"),
            "binding_status": database_design.get("binding_status"),
            "bindings": [
                {
                    "entity_field": str(binding.get("entity_field") or ""),
                    "table": str(binding.get("table") or ""),
                    "table_column": str(binding.get("table_column") or ""),
                    "rule": str(binding.get("rule") or ""),
                }
                for binding in bindings[:100]
                if binding.get("entity_field") and binding.get("table_column")
            ],
            "table_generation_required": bool(table_generation.get("required")),
            "table_generation_approved": bool(table_generation.get("approved")),
            "execution": {
                "status": str(database_execution.get("status") or ""),
                "summary": str(database_execution.get("summary") or "")[:500],
                "operation_count": len(database_execution.get("operation_ids") or []),
            },
        }
    external_api_design = (
        detail.get("external_api_design")
        if isinstance(detail.get("external_api_design"), dict)
        else {}
    )
    if external_api_design:
        required_fields = {
            str(field.get("name") or "")
            for field in fields
            if isinstance(field, dict) and bool(field.get("required"))
        }
        connection = (
            external_api_design.get("connection")
            if isinstance(external_api_design.get("connection"), dict)
            else {}
        )
        operation_summaries: list[dict[str, Any]] = []
        for operation in _design_items(external_api_design.get("operations")):
            refs = _design_items(operation.get("endpoint_refs"))
            operation_ref_keys = {
                (
                    str(ref.get("api_contract_id") or ""),
                    str(ref.get("endpoint_id") or ""),
                )
                for ref in refs
            }
            if endpoint_refs is not None and not (operation_ref_keys & endpoint_refs):
                continue
            api_info = operation.get("api_info") if isinstance(operation.get("api_info"), dict) else {}
            response_handling = operation.get("response_handling") if isinstance(operation.get("response_handling"), dict) else {}
            mappings = _design_items(operation.get("field_mappings"))
            mapped_required = {
                str(mapping.get("entity_field") or "")
                for mapping in mappings
                if str(mapping.get("source_field") or "").strip()
            }
            request_body = _external_json_value(api_info.get("request_body"))
            response_body = _external_json_value(api_info.get("response_body"))
            override = operation.get("connection_override") if isinstance(operation.get("connection_override"), dict) else {}
            operation_headers = _design_items(api_info.get("headers"))
            effective_headers = {
                str(item.get("name") or "").casefold(): dict(item)
                for item in _design_items(connection.get("headers"))
                if str(item.get("name") or "").strip()
            }
            for header in operation_headers:
                name = str(header.get("name") or "").strip()
                if name:
                    effective_headers[name.casefold()] = dict(header)
            operation_summaries.append(
                {
                    "operation_id": operation.get("operation_id"),
                    "name": operation.get("name"),
                    "endpoint_refs": refs,
                    "effective_connection": {
                        "base_url": override.get("base_url") or connection.get("base_url"),
                        "base_url_config_key": override.get("base_url_config_key") or connection.get("base_url_config_key"),
                        "timeout_ms": override.get("timeout_ms") or connection.get("timeout_ms"),
                        "headers": list(effective_headers.values()),
                    },
                    "api_info": {
                        "method": api_info.get("method"),
                        "path": api_info.get("path"),
                        "parameters": _bounded_external_items(api_info.get("parameters")),
                        "headers": _bounded_external_items(api_info.get("headers")),
                        "request_shape": _external_json_shape(request_body),
                        "response_shape": _external_json_shape(response_body),
                    },
                    "response_handling": _bounded_external_object(response_handling),
                    "field_mappings": _bounded_external_items(mappings),
                    "mapped_entity_path": _mapped_entity_path(mappings),
                    "mapping_count": len(mappings),
                    "required_field_count": len(required_fields),
                    "mapped_required_count": len(required_fields & mapped_required),
                }
            )
        summary["external_api_design"] = {
            "connection": _bounded_external_connection(connection),
            "operation_count": len(operation_summaries),
            "operations": operation_summaries,
        }
    static_design = (
        detail.get("static_design")
        if isinstance(detail.get("static_design"), dict)
        else {}
    )
    if static_design:
        summary["static_design"] = {
            "seed_row_count": len(_design_items(static_design.get("seed_rows"))),
            "field_value_count": len(static_design.get("field_values") or {}),
        }
    return summary


def _design_items(value: Any) -> list[dict[str, Any]]:
    """只保留列表中的字典项，用于读取实体设计各方案段。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _bounded_external_items(value: Any, limit: int = 200) -> list[dict[str, Any]]:
    """限制外部 API 参数、Header 和映射摘要的条数并复制为稳定结构。"""

    return [dict(item) for item in _design_items(value)[:limit]]


def _bounded_external_connection(value: Any) -> dict[str, Any]:
    """投射外部 API 共享连接的全部非敏感生成语义。"""

    if not isinstance(value, dict):
        return {}
    return {
        "base_url": str(value.get("base_url") or "")[:2000],
        "base_url_config_key": str(value.get("base_url_config_key") or "")[:128],
        "timeout_ms": value.get("timeout_ms"),
        "headers": _bounded_external_items(value.get("headers"), 50),
    }


def _bounded_external_object(value: Any) -> dict[str, Any]:
    """只投影外部 API 响应处理语义，避免把样例值带入构建上下文。"""

    if not isinstance(value, dict):
        return {}
    allowed = {
        "entity_payload",
        "cardinality",
        "payload_path",
        "success_status_codes",
        "error_message_path",
        "total_path",
        "pagination",
    }
    return {key: value[key] for key in allowed if key in value}


def _external_json_value(value: Any) -> Any:
    """把已确认外部 API JSON 文本解析为结构值，非法文本按空值处理。"""

    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _external_json_type(value: Any) -> str:
    """返回外部 API 样例的稳定基础类型，区分整数与小数。"""

    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "decimal"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return "string"


def _external_json_shape(
    value: Any,
    max_depth: int = 6,
    max_paths: int = 300,
) -> dict[str, Any]:
    """把请求或响应样例裁剪为根类型和有界路径类型，不暴露业务样例值。"""

    fields: list[dict[str, str]] = []

    def append(path: str, item_type: str) -> None:
        """按首次出现顺序记录字段，并把数组样例中的整数/小数合并为小数。"""

        if not path:
            return
        existing = next((item for item in fields if item["path"] == path), None)
        if existing is not None:
            current_type = existing["type"]
            if {current_type, item_type} == {"integer", "decimal"}:
                existing["type"] = "decimal"
            elif current_type == "null" and item_type != "null":
                existing["type"] = item_type
            return
        if len(fields) < max_paths:
            fields.append({"path": path[:2000], "type": item_type})

    def visit(node: Any, prefix: str, depth: int) -> None:
        """递归展开对象和代表性数组元素，并统一附加数组方括号。"""

        if depth > max_depth or len(fields) >= max_paths:
            return
        if isinstance(node, list):
            array_path = prefix if prefix.endswith("[]") else f"{prefix}[]"
            append(array_path or "[]", "array")
            for child in node[:20]:
                visit(child, array_path or "[]", depth + 1)
            return
        if not isinstance(node, dict):
            return
        for key, child in node.items():
            if len(fields) >= max_paths:
                break
            normalized_key = str(key).strip()[:200]
            if not normalized_key:
                continue
            path = f"{prefix}.{normalized_key}" if prefix else normalized_key
            if isinstance(child, list):
                visit(child, f"{path}[]", depth + 1)
                continue
            append(path, _external_json_type(child))
            if isinstance(child, dict):
                visit(child, path, depth + 1)

    visit(value, "", 0)
    return {"root_type": _external_json_type(value), "fields": fields}


def _mapped_entity_path(mappings: list[dict[str, Any]]) -> str:
    """从全部来源字段确定共同且最深的数组前缀，供映射层逐项提取实体。"""

    prefix_sets: list[set[str]] = []
    for mapping in mappings:
        source_field = str(mapping.get("source_field") or "").strip()
        if not source_field:
            continue
        parts = [part for part in source_field.split(".") if part]
        prefixes = {
            ".".join(parts[: index + 1])
            for index, part in enumerate(parts)
            if part.endswith("[]")
        }
        prefix_sets.append(prefixes)
    if not prefix_sets:
        return ""
    common = set.intersection(*prefix_sets)
    return max(common, key=lambda item: (item.count("."), len(item)), default="")


def normalize_entity_field(
    value: Any,
    index: int,
    *,
    with_types: bool = False,
) -> dict[str, Any] | None:
    """归一化单个实体字段；需求层只保留展示信息，规划层才生成字段名与语义类型。"""

    if not isinstance(value, dict):
        return None
    label = str(value.get("label") or value.get("name") or "").strip()
    normalized: dict[str, Any] = {
        "label": label,
        "description": str(value.get("description") or ""),
    }
    if with_types:
        name = str(value.get("name") or "").strip()
        if not ENTITY_FIELD_NAME_RE.match(name):
            name = _derived_field_name(label, index)
        if name == RESERVED_ENTITY_FIELD:
            name = _derived_field_name(label, index)
        field_type = str(value.get("type") or "text").strip()
        if field_type not in ENTITY_FIELD_TYPES:
            field_type = "text"
        enum_values = (
            [
                str(item).strip()
                for item in value.get("enum_values")
                if isinstance(item, str) and str(item).strip()
            ]
            if isinstance(value.get("enum_values"), list)
            else []
        )
        normalized.update(
            {
                "name": name,
                "type": field_type,
                "required": bool(value.get("required")),
            }
        )
        if enum_values:
            normalized["enum_values"] = enum_values
    return normalized


def normalize_entity(
    value: Any,
    index: int,
    *,
    with_types: bool = False,
) -> dict[str, Any]:
    """把字符串或字典实体归一化为稳定的实体对象。"""

    if isinstance(value, str):
        entity_id = value.strip()
        return {
            "id": entity_id,
            "name": entity_id,
            "description": "",
            "fields": [],
        }
    if not isinstance(value, dict):
        return {
            "id": f"Entity{index + 1}",
            "name": f"Entity{index + 1}",
            "description": "",
            "fields": [],
        }
    entity_id = str(value.get("id") or value.get("name") or f"Entity{index + 1}").strip()
    name = str(value.get("name") or entity_id).strip()
    fields: list[dict[str, Any]] = []
    seen_fields: set[str] = set()
    for field_index, field in enumerate(value.get("fields") if isinstance(value.get("fields"), list) else []):
        normalized_field = normalize_entity_field(
            field,
            field_index,
            with_types=with_types,
        )
        if normalized_field is None:
            continue
        # 无论哪一层都以展示名称去重，避免同一信息项被重复设计。
        field_identity = str(normalized_field.get("label") or "").strip()
        if not field_identity or field_identity in seen_fields:
            continue
        seen_fields.add(field_identity)
        fields.append(normalized_field)
    normalized: dict[str, Any] = {
        "id": entity_id,
        "name": name,
        "description": str(value.get("description") or ""),
        "fields": fields,
    }
    module_id = str(value.get("module_id") or "").strip()
    if module_id:
        normalized["module_id"] = module_id
    # 计划阶段实体不携带 data_source；数据源在实体设计阶段选择并确认。
    normalized.pop("data_source", None)
    return normalized


def normalize_entities(
    value: Any,
    *,
    with_types: bool = False,
) -> list[dict[str, Any]]:
    """规范化实体列表，去重实体 id 并保持旧字符串兼容。"""

    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(value if isinstance(value, list) else []):
        normalized = normalize_entity(item, index, with_types=with_types)
        entity_id = normalized["id"]
        if not entity_id or entity_id in seen_ids:
            continue
        seen_ids.add(entity_id)
        result.append(normalized)
    return result


def merge_entities(
    existing: Any,
    incoming: Any,
    *,
    with_types: bool = False,
) -> list[dict[str, Any]]:
    """合并实体时保留已有稳定 id，并用新模型或编辑器内容覆盖业务字段。"""

    existing_entities = normalize_entities(existing, with_types=with_types)
    incoming_entities = normalize_entities(incoming, with_types=with_types)
    if not existing_entities:
        return incoming_entities
    existing_by_id = {
        str(item.get("id") or "").lower(): item for item in existing_entities
    }
    existing_by_name = {
        str(item.get("name") or "").strip().lower(): item for item in existing_entities
    }
    merged: list[dict[str, Any]] = []
    for item in incoming_entities:
        stable = (
            existing_by_id.get(str(item.get("id") or "").lower())
            or existing_by_name.get(str(item.get("name") or "").strip().lower())
        )
        if stable is None:
            merged.append(item)
            continue
        fields = item.get("fields") if item.get("fields") else stable.get("fields", [])
        merged.append(
            {
                "id": str(stable.get("id") or item.get("id") or ""),
                "name": item.get("name") or stable.get("name") or "",
                "description": item.get("description") or stable.get("description") or "",
                "fields": fields,
                **(
                    {"module_id": item.get("module_id") or stable.get("module_id")}
                    if item.get("module_id") or stable.get("module_id")
                    else {}
                ),
            }
        )
    return merged


def validate_entity_definitions(
    entities: Any,
    *,
    with_types: bool = False,
) -> list[str]:
    """校验原始实体定义结构，返回可读错误；不替代归一化。"""

    errors: list[str] = []
    seen_ids: set[str] = set()
    for entity in entities if isinstance(entities, list) else []:
        entity_id = ""
        fields: list[Any] = []
        if isinstance(entity, str):
            entity_id = entity.strip()
        elif isinstance(entity, dict):
            entity_id = str(entity.get("id") or entity.get("name") or "").strip()
            fields = entity.get("fields") if isinstance(entity.get("fields"), list) else []
        if not entity_id:
            errors.append("实体 id 不能为空。")
            continue
        if entity_id in seen_ids:
            errors.append(f"实体 id 重复：{entity_id}。")
        seen_ids.add(entity_id)
        seen_fields: set[str] = set()
        for field in fields:
            if not isinstance(field, dict):
                continue
            if with_types:
                field_name = str(field.get("name") or "").strip()
                if not field_name:
                    errors.append(f"实体 {entity_id} 存在空字段名。")
                    continue
                if field_name == RESERVED_ENTITY_FIELD:
                    errors.append(f"实体 {entity_id} 使用了保留字段 {RESERVED_ENTITY_FIELD}。")
                if not ENTITY_FIELD_NAME_RE.match(field_name):
                    errors.append(f"实体 {entity_id} 字段名非法：{field_name}。")
                if field_name in seen_fields:
                    errors.append(f"实体 {entity_id} 字段名重复：{field_name}。")
                seen_fields.add(field_name)
                field_type = str(field.get("type") or "").strip()
                if field_type not in ENTITY_FIELD_TYPES:
                    errors.append(f"实体 {entity_id} 字段 {field_name} 类型非法：{field_type}。")
            elif not str(field.get("label") or "").strip():
                errors.append(f"实体 {entity_id} 存在空的信息项名称。")
    return errors


def entity_json_schema(entity: Any) -> dict[str, Any]:
    """从业务实体字段派生 API 契约 Schema，id 作为隐式主键追加。"""

    normalized = normalize_entity(entity, 0, with_types=True)
    properties: dict[str, Any] = {
        "id": {"type": "string"},
    }
    required: list[str] = ["id"]
    for field in normalized.get("fields", []):
        # 实体字段名以模型为准（可能为 id）；隐式主键 id 已存在，避免重复定义。
        if str(field.get("name") or "") == RESERVED_ENTITY_FIELD:
            continue
        field_type = str(field.get("type") or "text")
        property_schema: dict[str, Any] = {
            "type": JSON_TYPE_BY_FIELD_TYPE.get(field_type, "string")
        }
        if field_type in JSON_FORMAT_BY_FIELD_TYPE:
            property_schema["format"] = JSON_FORMAT_BY_FIELD_TYPE[field_type]
        if field_type == "enum" and field.get("enum_values"):
            property_schema["enum"] = list(field["enum_values"])
        properties[field["name"]] = property_schema
        if field.get("required"):
            required.append(field["name"])
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def entity_mysql_target_table(entity: Any) -> dict[str, Any]:
    """把业务实体确定性编译为目标 MySQL 表结构，作为 schema diff 的基线。"""

    normalized = normalize_entity(entity, 0, with_types=True)
    table_name = entity_table_name(str(normalized.get("id") or ""))
    columns: list[dict[str, Any]] = [
        {
            "name": "id",
            "type": "BIGINT",
            "nullable": False,
            "default": None,
            "comment": "主键",
            "auto_increment": True,
        }
    ]
    for field in normalized.get("fields", []):
        # 隐式主键 id 列已存在；实体中同名字段直接由该列承载，避免重复列。
        if str(field.get("name") or "") == RESERVED_ENTITY_FIELD:
            continue
        columns.append(
            {
                "name": field["name"],
                "type": MYSQL_TYPE_BY_FIELD_TYPE.get(str(field.get("type") or "text"), "VARCHAR(255)"),
                "nullable": not bool(field.get("required")),
                "default": None,
                "comment": str(field.get("label") or field.get("description") or field["name"]),
            }
        )
    return {
        "name": table_name,
        "comment": str(normalized.get("description") or normalized.get("name") or table_name),
        "columns": columns,
        "primary_key": ["id"],
        "indexes": [],
        "foreign_keys": [],
        "source": "entity_definition",
    }


def database_operation_field_errors(
    entities: Any,
    data_origin: Any,
) -> list[str]:
    """校验数据库操作字段与已确认实体一致，防止详情阶段发明字段。"""

    normalized_entities = normalize_entities(entities, with_types=True)
    if not normalized_entities:
        return []
    entity_tables = {
        entity_table_name(str(item.get("id") or "")): item for item in normalized_entities
    }
    allowed_columns: set[str] = {RESERVED_ENTITY_FIELD}
    for entity in normalized_entities:
        for field in entity.get("fields", []):
            allowed_columns.add(str(field.get("name") or ""))
    origin = data_origin if isinstance(data_origin, dict) else {}
    effective_source = origin.get("effective_source")
    effective_source = effective_source if isinstance(effective_source, dict) else {}
    kind = str(effective_source.get("kind") or "")
    # 实体设计确认的目标表允许执行补列等既有表操作；新建表仍需规范表名。
    matched_table = str(origin.get("matched_table") or "").strip()
    errors: list[str] = []
    for operation in origin.get("database_operations") if isinstance(origin.get("database_operations"), list) else []:
        if not isinstance(operation, dict):
            continue
        operation_kind = str(operation.get("operation") or "")
        raw_table = operation.get("table")
        if operation_kind == "create_table" and isinstance(raw_table, dict):
            table_name = str(raw_table.get("name") or "").strip()
            if table_name not in entity_tables:
                errors.append(
                    f"create_table 表名 {table_name or '空'} 不是实体定义的目标表，"
                    f"可用表名：{', '.join(sorted(entity_tables))}。"
                )
            for column in raw_table.get("columns") if isinstance(raw_table.get("columns"), list) else []:
                if not isinstance(column, dict):
                    continue
                column_name = str(column.get("name") or "").strip()
                if column_name and column_name not in allowed_columns:
                    errors.append(
                        f"create_table 字段 {table_name}.{column_name} 未在实体定义中。"
                    )
            continue
        table_name = str(raw_table or "").strip()
        if table_name and table_name not in entity_tables and table_name != matched_table:
            errors.append(f"数据库操作表名 {table_name or '空'} 不是实体定义的目标表。")
        column_name = str(operation.get("column") or "").strip()
        if column_name and column_name not in allowed_columns:
            errors.append(
                f"{operation_kind} 字段 {table_name}.{column_name} 未在实体定义中。"
            )
    return errors
