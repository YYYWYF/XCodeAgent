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
    """从已确认实体设计与 API Contract 推导目标数据库结构。"""

    tables: dict[str, dict[str, Any]] = {}
    resolution_items: list[dict[str, Any]] = []
    database = ""
    for target in targets:
        entity_designs = _entity_design_items(target.get("entity_designs"))
        operations = _entity_design_operations(entity_designs)
        database = database or _entity_design_database_name(entity_designs)
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
            _merge_entity_design_columns(table, entity_designs, table_name)
        _merge_database_operations(tables, operations, target)
        resolution_items.extend(
            _entity_design_resolution_items(entity_designs, target)
        )
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


def _entity_design_items(value: Any) -> list[dict[str, Any]]:
    """把 target.entity_designs 规范为字典列表。"""

    return dict_items(value)


def _database_design(entity_design: dict[str, Any]) -> dict[str, Any]:
    """读取实体设计中的数据库方案，缺失时返回空对象。"""

    return (
        entity_design.get("database_design")
        if isinstance(entity_design.get("database_design"), dict)
        else {}
    )


def _entity_design_database_name(entity_designs: list[dict[str, Any]]) -> str:
    """从实体设计的数据库方案读取目标数据库名。"""

    for entity_design in entity_designs:
        database_design = _database_design(entity_design)
        database_name = str(database_design.get("database_name") or "").strip()
        if database_name:
            return database_name
        schema_context = (
            database_design.get("schema_context")
            if isinstance(database_design.get("schema_context"), dict)
            else {}
        )
        database_name = str(schema_context.get("database") or "").strip()
        if database_name:
            return database_name
    return ""


def _target_table_names(target: dict[str, Any]) -> list[str]:
    """按实体设计的目标表与绑定反查表名，接口不再携带数据源信息。"""

    names: list[str] = []
    for entity_design in _entity_design_items(target.get("entity_designs")):
        entity_id = str(entity_design.get("entity_id") or "").strip()
        database_design = _database_design(entity_design)
        matched_table = str(database_design.get("matched_table") or "").strip()
        binding_tables = [
            str(binding.get("table") or "").strip()
            for binding in dict_items(database_design.get("bindings"))
            if str(binding.get("table") or "").strip()
        ]
        if matched_table or binding_tables:
            names.extend([matched_table, *binding_tables])
            continue
        if entity_id:
            names.append(entity_table_name(entity_id))
    return list(dict.fromkeys(name for name in names if name))


def _merge_entity_design_columns(
    table: dict[str, Any],
    entity_designs: list[dict[str, Any]],
    table_name: str,
) -> None:
    """从实体设计的字段与表绑定编译目标表列，接口不参与列定义。"""

    columns = table["columns"]
    for entity_design in entity_designs:
        entity_id = str(entity_design.get("entity_id") or "").strip()
        database_design = _database_design(entity_design)
        for binding in dict_items(database_design.get("bindings")):
            binding_table = str(binding.get("table") or "").strip()
            column_name = str(
                binding.get("table_column")
                or binding.get("column")
                or binding.get("column_name")
                or ""
            ).strip()
            if not column_name:
                continue
            if binding_table and binding_table != table_name:
                continue
            columns.setdefault(
                column_name,
                {
                    "name": column_name,
                    "source": "entity_design_binding",
                    "source_evidence": binding,
                },
            )
        if table_name == entity_table_name(entity_id):
            for field in dict_items(entity_design.get("fields")):
                field_name = str(field.get("name") or "").strip()
                if not field_name:
                    continue
                columns.setdefault(
                    field_name,
                    {
                        "name": field_name,
                        "type": str(field.get("column_type") or ""),
                        "nullable": not bool(field.get("required")),
                        "source": "entity_design",
                        "source_evidence": field,
                    },
                )


def _entity_design_operations(
    entity_designs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """汇总实体设计已确认的数据库结构操作。"""

    operations: list[dict[str, Any]] = []
    for entity_design in entity_designs:
        operations.extend(
            dict_items(_database_design(entity_design).get("database_operations"))
        )
    return operations


def _entity_design_resolution_items(
    entity_designs: list[dict[str, Any]],
    target: dict[str, Any],
) -> list[dict[str, Any]]:
    """把实体设计中的结构化差异投射为数据库上下文的处理建议。"""

    items: list[dict[str, Any]] = []
    source_refs = [
        {
            "api_contract_id": target.get("api_contract_id"),
            "endpoint_id": target.get("endpoint_id"),
            "data_source_id": target.get("data_source_id"),
        }
    ]
    for entity_design in entity_designs:
        for difference in dict_items(
            _database_design(entity_design).get("differences")
        ):
            field = str(difference.get("field") or "").strip()
            items.append(
                {
                    "resolution_kind": str(
                        difference.get("kind")
                        or difference.get("resolution_kind")
                        or "needs_user_confirmation"
                    ),
                    "target_field": field,
                    "operation_refs": text_items(difference.get("operation_refs")),
                    "backend_adaptation": difference.get("backend_adaptation"),
                    "message": str(
                        difference.get("resolution")
                        or difference.get("actual")
                        or difference.get("expected")
                        or ""
                    ),
                    "source_refs": source_refs,
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
