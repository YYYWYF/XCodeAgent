from __future__ import annotations

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
    """从实体数据源类型推导数据源列表（按类型聚合，无独立数据源 id/name）。"""

    sources: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entity in (
        project_plan.get("entities") or []
        if isinstance(project_plan, dict)
        else []
    ):
        if not isinstance(entity, dict):
            continue
        source_type = normalize_data_source_type(entity.get("data_source"))
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
    data_source_type = normalize_data_source_type(value.get("data_source"))
    if data_source_type:
        normalized["data_source"] = data_source_type
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
                dict(
                    {
                        "id": str(stable.get("id") or item.get("id") or ""),
                        "name": item.get("name") or stable.get("name") or "",
                        "description": item.get("description")
                        or stable.get("description")
                        or "",
                        "fields": fields,
                    },
                    **(
                        {"module_id": item.get("module_id") or stable.get("module_id")}
                        if item.get("module_id") or stable.get("module_id")
                        else {}
                    ),
                    **(
                        {
                            "data_source": item.get("data_source")
                            or stable.get("data_source")
                        }
                        if item.get("data_source") or stable.get("data_source")
                        else {}
                    ),
                )
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
        if table_name and table_name not in entity_tables:
            errors.append(f"数据库操作表名 {table_name or '空'} 不是实体定义的目标表。")
        column_name = str(operation.get("column") or "").strip()
        if column_name and column_name not in allowed_columns:
            errors.append(
                f"{operation_kind} 字段 {table_name}.{column_name} 未在实体定义中。"
            )
    return errors
