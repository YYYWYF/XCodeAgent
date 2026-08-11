from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from app.services.entity_definitions import (
    entity_mysql_target_table,
    entity_table_name,
    normalize_entities,
)
from app.services.database_schema_summary import dict_items, text_items


def derive_required_database_schema(
    targets: list[dict[str, Any]],
    data_sources: list[dict[str, Any]] | None = None,
    new_table_entity_ids: set[str] | None = None,
) -> dict[str, Any]:
    """从已确认接口详情、实体定义和 API Contract 推导目标数据库结构。"""

    tables: dict[str, dict[str, Any]] = {}
    resolution_items: list[dict[str, Any]] = []
    database = ""
    for target in targets:
        detail = target.get("endpoint_detail")
        detail = detail if isinstance(detail, dict) else {}
        data_origin = detail.get("data_origin")
        data_origin = data_origin if isinstance(data_origin, dict) else {}
        effective_source = data_origin.get("effective_source")
        effective_source = (
            effective_source if isinstance(effective_source, dict) else {}
        )
        operations = dict_items(data_origin.get("database_operations"))
        database = database or str(effective_source.get("database") or "")
        database = database or next(
            (
                str(item.get("database") or "")
                for item in operations
                if item.get("database")
            ),
            "",
        )
        table_names = _target_table_names(target)
        for table_name in table_names:
            table = _required_table(tables, table_name)
            table["source_refs"].append(
                {
                    "api_contract_id": target.get("api_contract_id"),
                    "endpoint_id": target.get("endpoint_id"),
                    "method": target.get("method"),
                    "path": target.get("path"),
                    "data_source_id": target.get("data_source_id"),
                }
            )
            _merge_mapping_columns(table, data_origin, table_name)
        _merge_database_operations(tables, operations, target)
        resolution_items.extend(_structured_resolution_items(data_origin, target))
    if isinstance(data_sources, list):
        in_scope_source_ids = {
            str(target.get("data_source_id") or "") for target in targets
        }
        _merge_entity_target_tables(
            tables,
            data_sources,
            in_scope_source_ids,
            new_table_entity_ids,
        )
    normalized_tables = [_normalize_table(table) for table in tables.values()]
    payload = {
        "database": database,
        "tables": normalized_tables,
        "resolution_items": _dedupe_dicts(resolution_items),
    }
    return {
        **payload,
        "schema_hash": _stable_hash(payload),
    }


def _merge_entity_target_tables(
    tables: dict[str, dict[str, Any]],
    data_sources: list[dict[str, Any]],
    in_scope_source_ids: set[str],
    new_table_entity_ids: set[str],
) -> None:
    """把建表目标的实体定义编译为目标表基线，操作列保持优先。"""

    for source in data_sources:
        source_id = str(source.get("id") or "")
        if source_id not in in_scope_source_ids:
            continue
        if str(source.get("type") or "") != "database":
            continue
        for entity in normalize_entities(source.get("entities"), with_types=True):
            entity_id = str(entity.get("id") or "")
            if not entity_id:
                continue
            if new_table_entity_ids is not None and entity_id not in new_table_entity_ids:
                continue
            table_name = entity_table_name(entity_id)
            entity_table = entity_mysql_target_table(entity)
            table = _required_table(tables, table_name)
            table.setdefault("comment", entity_table.get("comment") or "")
            table.setdefault("primary_key", entity_table.get("primary_key") or [])
            table["source_refs"].append(
                {
                    "source": "entity_definition",
                    "data_source_id": source_id,
                    "entity_id": entity_id,
                }
            )
            for column in entity_table.get("columns", []):
                column_name = str(column.get("name") or "")
                if not column_name or column_name in table["columns"]:
                    continue
                table["columns"][column_name] = {
                    **column,
                    "source": "entity_definition",
                    "source_evidence": entity_table,
                    "source_refs": [
                        {
                            "source": "entity_definition",
                            "data_source_id": source_id,
                            "entity_id": entity_id,
                        }
                    ],
                }


def _target_table_names(target: dict[str, Any]) -> list[str]:
    """按 EndpointDetail.data_origin 优先读取目标表名，不用 ProjectPlan 臆测字段。"""

    detail = target.get("endpoint_detail")
    detail = detail if isinstance(detail, dict) else {}
    data_origin = detail.get("data_origin")
    data_origin = data_origin if isinstance(data_origin, dict) else {}
    effective_source = data_origin.get("effective_source")
    effective_source = effective_source if isinstance(effective_source, dict) else {}
    names = text_items(
        effective_source.get("tables")
        or effective_source.get("table_names")
        or data_origin.get("tables")
        or data_origin.get("table_names")
    )
    table = str(effective_source.get("table") or data_origin.get("table") or "").strip()
    return list(dict.fromkeys([*names, *([table] if table else [])]))


def _merge_mapping_columns(
    table: dict[str, Any],
    data_origin: dict[str, Any],
    table_name: str,
) -> None:
    """从 field_mappings 中提取明确存在的数据库列。"""

    columns = table["columns"]
    for mapping in dict_items(data_origin.get("field_mappings")):
        mapping_table = str(
            mapping.get("table")
            or mapping.get("table_name")
            or mapping.get("target_table")
            or ""
        ).strip()
        column_name = str(
            mapping.get("column")
            or mapping.get("column_name")
            or mapping.get("target_column")
            or mapping.get("db_column")
            or ""
        ).strip()
        if not column_name:
            parsed = _source_table_column(mapping, table_name)
            if parsed:
                mapping_table, column_name = parsed
        if mapping_table and mapping_table != table_name:
            continue
        if not column_name:
            continue
        columns.setdefault(
            column_name,
            {
                "name": column_name,
                "type": str(
                    mapping.get("mysql_type") or mapping.get("column_type") or ""
                ),
                "nullable": mapping.get("nullable"),
                "source": "field_mapping",
                "source_evidence": mapping,
            },
        )


def _source_table_column(
    mapping: dict[str, Any],
    table_name: str,
) -> tuple[str, str] | None:
    """从自然语言映射中识别 table.column 形式的真实数据库列。"""

    source = str(mapping.get("source") or "")
    marker = f"{table_name}."
    if marker not in source:
        return None
    remainder = source.split(marker, 1)[1]
    column = ""
    for char in remainder:
        if char.isalnum() or char == "_":
            column += char
            continue
        break
    return (table_name, column) if column else None


def _structured_resolution_items(
    data_origin: dict[str, Any],
    target: dict[str, Any],
) -> list[dict[str, Any]]:
    """把结构化字段决策投射为数据库上下文的处理建议。"""

    items: list[dict[str, Any]] = []
    for difference in dict_items(data_origin.get("differences")):
        field = str(difference.get("field") or "").strip()
        items.append(
            {
                "resolution_kind": str(difference.get("resolution_kind") or ""),
                "target_field": field,
                "operation_refs": text_items(difference.get("operation_refs")),
                "backend_adaptation": difference.get("backend_adaptation"),
                "message": str(
                    difference.get("actual") or difference.get("expected") or ""
                ),
                "source_refs": [
                    {
                        "api_contract_id": target.get("api_contract_id"),
                        "endpoint_id": target.get("endpoint_id"),
                        "data_source_id": target.get("data_source_id"),
                    }
                ],
                "source_evidence": difference,
            }
        )
    return items


def _merge_database_operations(
    tables: dict[str, dict[str, Any]],
    operations: list[dict[str, Any]],
    target: dict[str, Any],
) -> None:
    """把已确认数据库操作编译为后续 Schema Diff 使用的目标表结构。"""

    source_ref = {
        "api_contract_id": target.get("api_contract_id"),
        "endpoint_id": target.get("endpoint_id"),
        "data_source_id": target.get("data_source_id"),
    }
    for operation in operations:
        kind = str(operation.get("operation") or "")
        raw_table = operation.get("table")
        if kind == "create_table" and isinstance(raw_table, dict):
            table_name = str(raw_table.get("name") or "")
            if not table_name:
                continue
            table = _required_table(tables, table_name)
            table["source_refs"].append(source_ref)
            table["comment"] = str(raw_table.get("comment") or "")
            table["primary_key"] = text_items(raw_table.get("primary_key"))
            table["indexes"] = dict_items(raw_table.get("indexes"))
            table["foreign_keys"] = dict_items(raw_table.get("foreign_keys"))
            for column in dict_items(raw_table.get("columns")):
                _merge_operation_column(table, column, operation, source_ref)
            continue
        table_name = str(raw_table or "")
        if not table_name:
            continue
        table = _required_table(tables, table_name)
        table["source_refs"].append(source_ref)
        column = _operation_required_column(operation)
        if column:
            _merge_operation_column(table, column, operation, source_ref)


def _required_table(
    tables: dict[str, dict[str, Any]],
    table_name: str,
) -> dict[str, Any]:
    """获取或创建规范化前的目标表累积结构。"""

    return tables.setdefault(
        table_name,
        {
            "name": table_name,
            "columns": {},
            "indexes": [],
            "foreign_keys": [],
            "source_refs": [],
        },
    )


def _operation_required_column(operation: dict[str, Any]) -> dict[str, Any]:
    """把字段级数据库操作转换为目标列定义。"""

    kind = str(operation.get("operation") or "")
    raw_column = operation.get("column")
    column_name = str(raw_column or "")
    target = operation.get("to") if isinstance(operation.get("to"), dict) else {}
    if not column_name:
        return {}
    column: dict[str, Any] = {"name": column_name}
    if kind == "add_column":
        column.update(target)
        return column
    target_key = {
        "alter_column_type": "type",
        "alter_column_nullable": "nullable",
        "alter_column_default": "default",
    }.get(kind)
    if target_key and target_key in target:
        column[target_key] = target.get(target_key)
    return column


def _merge_operation_column(
    table: dict[str, Any],
    column: dict[str, Any],
    operation: dict[str, Any],
    source_ref: dict[str, Any],
) -> None:
    """把单个操作的目标列合并到表结构并保留来源证据。"""

    column_name = str(column.get("name") or "")
    if not column_name:
        return
    existing = table["columns"].setdefault(column_name, {"name": column_name})
    existing.update({key: value for key, value in column.items() if key != "name"})
    existing["source"] = "database_operation"
    existing["source_evidence"] = operation
    existing["source_refs"] = [source_ref]


def _normalize_table(table: dict[str, Any]) -> dict[str, Any]:
    """把目标表结构排序成稳定输出，便于 diff 和哈希复用。"""

    columns = table.get("columns") if isinstance(table.get("columns"), dict) else {}
    return {
        **table,
        "columns": [columns[name] for name in sorted(columns)],
        "source_refs": _dedupe_dicts(table.get("source_refs")),
    }


def _dedupe_dicts(value: Any) -> list[dict[str, Any]]:
    """按 JSON 指纹去重字典列表并保留稳定顺序。"""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in dict_items(value):
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _stable_hash(value: Any) -> str:
    """为目标数据库结构生成稳定哈希。"""

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return sha256(encoded.encode("utf-8")).hexdigest()
