from __future__ import annotations

import json
from typing import Any

from app.tools.mysql_info import get_mysql_table_info


_MAX_TABLES = 12
_MAX_COLUMNS_PER_TABLE = 18


def inspect_mysql_schema(target: dict[str, Any]) -> dict[str, Any]:
    """调用受控 MySQL 工具并返回压缩后的数据库结构摘要。"""

    table_name = single_table_hint(target)
    try:
        # TODO: 当前阶段先由 get_mysql_table_info 从 .env 的 MYSQL_* 变量读取连接信息；
        # 后续改为通过 AG-UI 页面让用户输入或选择数据库连接，并在扫描/执行前确认。
        tool_result = get_mysql_table_info.invoke(
            {"table_name": table_name} if table_name else {"table_name": None}
        )
    except Exception as exc:
        return failed(
            "tool_exception",
            f"数据库工具执行异常：{type(exc).__name__}: {exc}",
            target=target,
        )
    return summarize_tool_result(str(tool_result), target=target, table_name=table_name)


def is_database_data_source(data_source: Any) -> bool:
    """根据 ProjectPlan 数据源声明判断是否需要数据库扫描。"""

    if not isinstance(data_source, dict) or not data_source:
        return False
    source_type = str(data_source.get("type") or data_source.get("source_type") or "").lower()
    database = str(data_source.get("database") or data_source.get("db") or "").lower()
    return (
        source_type in {"mysql", "database", "db"}
        or "mysql" in database
        or bool(data_source.get("tables") or data_source.get("table_names"))
    )


def single_table_hint(target: dict[str, Any]) -> str | None:
    """当 ProjectPlan 只声明一个目标表时限制工具扫描范围。"""

    data_source = target.get("data_source")
    if not isinstance(data_source, dict):
        return None
    table_names = text_items(
        data_source.get("tables")
        or data_source.get("table_names")
        or data_source.get("entities")
    )
    return table_names[0] if len(table_names) == 1 else None


def summarize_tool_result(
    raw_result: str,
    *,
    target: dict[str, Any],
    table_name: str | None,
) -> dict[str, Any]:
    """把数据库工具原始 JSON 压缩成模型可消费的安全摘要。"""

    try:
        payload = json.loads(raw_result)
    except json.JSONDecodeError as exc:
        return failed("invalid_tool_json", f"数据库工具返回内容不是合法 JSON：{exc}", target=target)

    if payload.get("status") != "ok":
        return failed(
            "tool_error",
            str(payload.get("error") or "数据库工具执行失败。"),
            target=target,
        )

    schemas = payload.get("schemas") if isinstance(payload.get("schemas"), dict) else {}
    table_comments = {
        str(table.get("table_name") or ""): str(table.get("comment") or "")
        for table in dict_items(payload.get("tables"))
    }
    summarized_tables = [
        _summarize_table(table, columns, table_comments.get(table, ""))
        for table, columns in list(schemas.items())[:_MAX_TABLES]
    ]
    return {
        "status": "completed",
        "enabled": True,
        "source": "get_mysql_table_info",
        "database": payload.get("database"),
        "target": target_summary(target),
        "scope": {
            "table_name": table_name,
            "table_count": len(schemas),
            "returned_table_count": len(summarized_tables),
            "truncated": len(schemas) > len(summarized_tables),
        },
        "summary": _human_summary(payload.get("database"), summarized_tables),
        "tables": summarized_tables,
    }


def failed(
    reason: str,
    message: str,
    *,
    target: dict[str, Any],
) -> dict[str, Any]:
    """构造扫描失败但不阻塞后续流程的统一状态。"""

    return {
        "status": "failed",
        "enabled": True,
        "reason": reason,
        "message": message,
        "target": target_summary(target),
    }


def target_summary(target: dict[str, Any] | None) -> dict[str, Any]:
    """移除不必要的大对象，只保留接口和数据源标识。"""

    if not isinstance(target, dict):
        return {}
    return {
        "api_contract_id": target.get("api_contract_id"),
        "endpoint_id": target.get("endpoint_id"),
        "method": target.get("method"),
        "path": target.get("path"),
        "data_source_id": target.get("data_source_id"),
    }


def dict_items(value: Any) -> list[dict[str, Any]]:
    """把可能的列表值规范成字典列表。"""

    return [item for item in value or [] if isinstance(item, dict)]


def text_items(value: Any) -> list[str]:
    """把表名或实体声明规范成字符串列表。"""

    result: list[str] = []
    for item in value or []:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        elif isinstance(item, dict):
            name = str(item.get("table_name") or item.get("name") or item.get("id") or "").strip()
            if name:
                result.append(name)
    return result


def _summarize_table(table_name: str, columns: Any, comment: str) -> dict[str, Any]:
    """提取单表字段摘要，并限制字段数量。"""

    column_items = dict_items(columns)
    return {
        "table_name": table_name,
        "comment": comment,
        "column_count": len(column_items),
        "columns": [
            {
                "name": str(column.get("column_name") or ""),
                "type": str(column.get("column_type") or ""),
                "nullable": str(column.get("is_nullable") or "").upper() == "YES",
                "key": str(column.get("column_key") or ""),
                "default": column.get("column_default"),
                "extra": str(column.get("extra") or ""),
                "comment": str(column.get("comment") or ""),
            }
            for column in column_items[:_MAX_COLUMNS_PER_TABLE]
        ],
        "truncated": len(column_items) > _MAX_COLUMNS_PER_TABLE,
    }


def _human_summary(database: Any, tables: list[dict[str, Any]]) -> str:
    """生成给模型阅读的一句话数据库概况。"""

    table_names = [str(table.get("table_name") or "") for table in tables]
    if not table_names:
        return f"数据库 {database or ''} 未返回可用表结构。".strip()
    return (
        f"数据库 {database or ''} 可参考表："
        f"{', '.join(table_names[:_MAX_TABLES])}。"
    ).strip()
