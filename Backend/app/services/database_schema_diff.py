from __future__ import annotations

from hashlib import sha256
import json
from typing import Any


def diff_database_schema(
    *,
    actual_schema: dict[str, Any],
    required_schema: dict[str, Any],
) -> list[dict[str, Any]]:
    """对比真实数据库结构和目标结构，把需改库的不足转成稳定 gap。"""

    gaps: list[dict[str, Any]] = []
    database = str(required_schema.get("database") or actual_schema.get("database") or "")
    if actual_schema.get("database_exists") is False:
        gaps.append(
            _gap(
                kind="missing_database",
                resolution_kind="database_change",
                database=database,
                table="",
                column="",
                message=f"数据库 {database} 不存在，需要先创建。",
                required={},
                actual={},
            )
        )
    actual_tables = _table_map(actual_schema.get("tables"))
    for required_table in _table_items(required_schema):
        table_name = str(required_table.get("name") or required_table.get("table_name") or "")
        actual_table = actual_tables.get(table_name.lower())
        if not actual_table:
            gaps.append(
                    _gap(
                        kind="missing_table",
                        resolution_kind="database_change",
                        database=database,
                        table=table_name,
                        column="",
                    message=f"表 {table_name} 不存在，需要创建。",
                    required=required_table,
                    actual={},
                )
            )
            continue
        actual_columns = _column_map(actual_table.get("columns"))
        for required_column in _column_items(required_table):
            column_name = str(required_column.get("name") or "")
            actual_column = actual_columns.get(column_name.lower())
            if not actual_column:
                gaps.append(
                        _gap(
                            kind="missing_column",
                            resolution_kind="database_change",
                            database=database,
                            table=table_name,
                            column=column_name,
                        message=f"表 {table_name} 缺少字段 {column_name}。",
                        required=required_column,
                        actual={},
                    )
                )
                continue
            type_gap = _type_gap(required_column, actual_column)
            if type_gap:
                gaps.append(
                        _gap(
                            kind="incompatible_column_type",
                            resolution_kind="database_change",
                            database=database,
                            table=table_name,
                        column=column_name,
                        message=(
                            f"表 {table_name}.{column_name} 类型不满足要求："
                            f"需要 {required_column.get('type')}，当前 {actual_column.get('type')}。"
                        ),
                        required=required_column,
                        actual=actual_column,
                    )
                )
            nullable_gap = _nullable_gap(required_column, actual_column)
            if nullable_gap:
                gaps.append(
                        _gap(
                            kind="nullable_mismatch",
                            resolution_kind="database_change",
                            database=database,
                            table=table_name,
                        column=column_name,
                        message=f"表 {table_name}.{column_name} nullable 与接口需求不一致。",
                        required=required_column,
                        actual=actual_column,
                    )
                )
    return gaps


def compile_database_task_intents(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 schema gap 编译为后续 Build DAG 必须覆盖的数据库任务意图。"""

    intents: list[dict[str, Any]] = []
    for gap in gaps:
        if gap.get("resolution_kind") != "database_change":
            continue
        kind = str(gap.get("kind") or "")
        task_type = "database.verify" if kind.endswith("_verify") else "database.change"
        operation = {
            "missing_database": "create_database",
            "missing_table": "create_table",
            "missing_column": "add_column",
            "incompatible_column_type": "alter_column",
            "nullable_mismatch": "alter_column_nullable",
        }.get(kind, "schema_change")
        risk = "medium" if operation.startswith("alter") else "low"
        intent = {
            "id": f"db-intent-{gap.get('id')}",
            "task_type": task_type,
            "operation": operation,
            "risk": risk,
            "gap_ids": [gap.get("id")],
            "database_scope": _database_scope(gap, operation),
            "description": str(gap.get("message") or ""),
        }
        intents.append(intent)
    return intents


def _gap(
    *,
    kind: str,
    resolution_kind: str,
    database: str,
    table: str,
    column: str,
    message: str,
    required: dict[str, Any],
    actual: dict[str, Any],
) -> dict[str, Any]:
    """构造带稳定 ID 的数据库结构差异。"""

    payload = {
        "kind": kind,
        "resolution_kind": resolution_kind,
        "database": database,
        "table": table,
        "column": column,
        "required": required,
        "actual": actual,
    }
    return {
        "id": sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode(
                "utf-8"
            )
        ).hexdigest()[:16],
        **payload,
        "message": message,
        "database_scope": _database_scope(
            {
                "kind": kind,
                "database": database,
                "table": table,
                "column": column,
                "required": required,
            },
            {
                "missing_database": "create_database",
                "missing_table": "create_table",
                "missing_column": "add_column",
                "incompatible_column_type": "alter_column",
                "nullable_mismatch": "alter_column_nullable",
            }.get(kind, "schema_change"),
        ),
        "source_evidence": required.get("source_evidence") or required.get("source_refs") or [],
    }


def _database_scope(gap: dict[str, Any], operation: str) -> dict[str, Any]:
    """从 gap 生成任务规划可直接复用的数据库范围。"""

    table = str(gap.get("table") or "")
    column = str(gap.get("column") or "")
    return {
        "database": gap.get("database"),
        "tables": [table] if table else [],
        "columns": [column] if column else [],
        "operations": [operation],
        "gap_ids": [gap.get("id")] if gap.get("id") else [],
        "gaps": [gap],
    }


def _table_items(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """读取 schema 中的表列表。"""

    value = schema.get("tables")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _table_map(value: Any) -> dict[str, dict[str, Any]]:
    """按小写表名索引真实表结构。"""

    result: dict[str, dict[str, Any]] = {}
    for table in value if isinstance(value, list) else []:
        if not isinstance(table, dict):
            continue
        name = str(table.get("name") or table.get("table_name") or "").lower()
        if name:
            result[name] = table
    return result


def _column_items(table: dict[str, Any]) -> list[dict[str, Any]]:
    """读取表中的目标字段列表。"""

    value = table.get("columns")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _column_map(value: Any) -> dict[str, dict[str, Any]]:
    """按小写字段名索引真实字段结构。"""

    result: dict[str, dict[str, Any]] = {}
    for column in value if isinstance(value, list) else []:
        if not isinstance(column, dict):
            continue
        name = str(column.get("name") or column.get("column_name") or "").lower()
        if name:
            result[name] = column
    return result


def _type_gap(required: dict[str, Any], actual: dict[str, Any]) -> bool:
    """用保守规则判断字段类型是否明显不兼容。"""

    required_type = _type_family(required.get("type"))
    actual_type = _type_family(actual.get("type") or actual.get("column_type"))
    return bool(required_type and actual_type and required_type != actual_type)


def _nullable_gap(required: dict[str, Any], actual: dict[str, Any]) -> bool:
    """判断字段 nullable 要求是否未被真实结构满足。"""

    if required.get("nullable") is not False:
        return False
    return actual.get("nullable") is True


def _type_family(value: Any) -> str:
    """把 MySQL 类型规约到少量兼容族，降低误报。"""

    text = str(value or "").lower()
    if any(item in text for item in ("int", "serial")):
        return "integer"
    if any(item in text for item in ("decimal", "numeric", "float", "double")):
        return "number"
    if any(item in text for item in ("datetime", "timestamp")):
        return "datetime"
    if "date" in text:
        return "date"
    if "json" in text:
        return "json"
    if any(item in text for item in ("char", "text", "enum")):
        return "string"
    if any(item in text for item in ("bool", "tinyint(1)")):
        return "boolean"
    return text
