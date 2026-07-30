from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from app.services.database_schema_summary import dict_items, text_items


def derive_required_database_schema(targets: list[dict[str, Any]]) -> dict[str, Any]:
    """从已确认接口详情和 API Contract 推导目标数据库结构与处理建议。"""

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
        origin_kind = str(
            effective_source.get("kind") or data_origin.get("source_type") or ""
        )
        database = database or str(effective_source.get("database") or "")
        table_names = _target_table_names(target)
        for table_name in table_names:
            table = tables.setdefault(
                table_name,
                {
                    "name": table_name,
                    "columns": {},
                    "indexes": [],
                    "foreign_keys": [],
                    "source_refs": [],
                },
            )
            table["source_refs"].append(
                {
                    "api_contract_id": target.get("api_contract_id"),
                    "endpoint_id": target.get("endpoint_id"),
                    "method": target.get("method"),
                    "path": target.get("path"),
                    "data_source_id": target.get("data_source_id"),
                }
            )
            resolution_items.extend(
                _merge_mapping_columns(table, data_origin, table_name)
            )
            if origin_kind == "mysql_new_table":
                _merge_contract_schema_columns(table, target)
        resolution_items.extend(_difference_resolution_items(data_origin, target))
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
) -> list[dict[str, Any]]:
    """从 field_mappings 中提取明确数据库列，并记录非改库处理建议。"""

    columns = table["columns"]
    resolution_items: list[dict[str, Any]] = []
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
            resolution_items.append(_mapping_resolution_item(mapping))
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
    return resolution_items


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


def _mapping_resolution_item(mapping: dict[str, Any]) -> dict[str, Any]:
    """把没有数据库列来源的字段映射归类为后端适配或待确认。"""

    text = json.dumps(mapping, ensure_ascii=False, default=str)
    if "待业务确认" in text or "是否需要新增" in text:
        kind = "needs_confirmation"
    elif (
        "无对应列" in text or "返回 null" in text or "返回0" in text or "返回 0" in text
    ):
        kind = "backend_adaptation"
    else:
        kind = "already_supported"
    return {
        "resolution_kind": kind,
        "target_field": mapping.get("target_field") or mapping.get("field"),
        "message": str(mapping.get("rule") or mapping.get("source") or ""),
        "source_evidence": mapping,
    }


def _difference_resolution_items(
    data_origin: dict[str, Any],
    target: dict[str, Any],
) -> list[dict[str, Any]]:
    """从 differences 中提取需要后端适配或业务确认的处理建议。"""

    items: list[dict[str, Any]] = []
    database = ""
    effective_source = data_origin.get("effective_source")
    if isinstance(effective_source, dict):
        database = str(effective_source.get("database") or "")
    table_names = text_items(
        effective_source.get("tables") if isinstance(effective_source, dict) else []
    )
    table = table_names[0] if table_names else ""
    for difference in dict_items(data_origin.get("differences")):
        text = json.dumps(difference, ensure_ascii=False, default=str)
        if "待业务确认" in text or "是否需要新增" in text:
            kind = "needs_confirmation"
        elif (
            "返回 null" in text or "返回0" in text or "返回 0" in text or "转换" in text
        ):
            kind = "backend_adaptation"
        elif "新增列" in text or "新增字段" in text or "create" in text.lower():
            kind = "database_change"
        else:
            kind = "backend_adaptation"
        field = str(difference.get("field") or "").strip()
        item = {
            "resolution_kind": kind,
            "database": database,
            "table": table,
            "column": field,
            "target_field": field,
            "message": str(
                difference.get("resolution") or difference.get("actual") or ""
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
        if kind == "database_change":
            item["database_scope"] = {
                "database": database,
                "tables": [table] if table else [],
                "columns": [field] if field else [],
                "operations": ["add_column"],
            }
        items.append(item)
    return items


def _merge_contract_schema_columns(
    table: dict[str, Any], target: dict[str, Any]
) -> None:
    """从 API Contract 请求/响应 schema 中补齐业务字段候选。"""

    contract = target.get("api_contract")
    contract = contract if isinstance(contract, dict) else {}
    endpoint = _target_endpoint(contract, target)
    schemas = (
        contract.get("schemas") if isinstance(contract.get("schemas"), dict) else {}
    )
    columns = table["columns"]
    for ref_key in ("request_schema_ref", "response_schema_ref"):
        schema_name = str(endpoint.get(ref_key) or "").split("#")[-1]
        schema = schemas.get(schema_name) if schema_name else None
        schema = schema if isinstance(schema, dict) else {}
        properties = (
            schema.get("properties")
            if isinstance(schema.get("properties"), dict)
            else {}
        )
        required = (
            schema.get("required") if isinstance(schema.get("required"), list) else []
        )
        for name, property_schema in properties.items():
            column_name = str(name).strip()
            if not column_name:
                continue
            prop = property_schema if isinstance(property_schema, dict) else {}
            columns.setdefault(
                column_name,
                {
                    "name": column_name,
                    "type": _mysql_type_from_schema(prop),
                    "nullable": column_name not in required,
                    "source": ref_key,
                },
            )


def _target_endpoint(
    contract: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    """在当前 API Contract 中找到目标 endpoint 的契约定义。"""

    endpoint_id = str(target.get("endpoint_id") or "")
    for endpoint in dict_items(contract.get("endpoints")):
        if str(endpoint.get("id") or "") == endpoint_id:
            return endpoint
    return {}


def _mysql_type_from_schema(schema: dict[str, Any]) -> str:
    """把接口 schema 的常见类型映射为保守 MySQL 类型。"""

    explicit = str(schema.get("mysql_type") or schema.get("column_type") or "").strip()
    if explicit:
        return explicit
    schema_type = str(schema.get("type") or "").lower()
    fmt = str(schema.get("format") or "").lower()
    if schema_type == "integer":
        return "bigint" if fmt == "int64" else "int"
    if schema_type == "number":
        return "decimal(18,2)"
    if schema_type == "boolean":
        return "tinyint(1)"
    if schema_type == "string" and fmt in {"date-time", "datetime"}:
        return "datetime"
    if schema_type == "string" and fmt == "date":
        return "date"
    if schema_type in {"object", "array"}:
        return "json"
    return "varchar(255)"


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
