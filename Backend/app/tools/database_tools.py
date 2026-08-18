"""受控数据库元数据工具：只读查询当前数据库的表清单与指定表字段。

实体设计单卡片化后，查表/选表属于高频本地操作，不进入 AG-UI 工作流往返；
本模块按既有 /tools/* 基础设施端点模式暴露只读元数据查询，供前端直接调用。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from app.services.database_schema_summary import (
    inspect_mysql_schema,
    inspect_mysql_table,
)


class DatabaseTablesRequest(BaseModel):
    """查询当前数据库表清单的请求。"""

    workspace_root: Optional[str] = Field(
        default=None,
        description="应用工作区根目录；缺失时返回连接失败状态。",
    )
    entity_id: Optional[str] = Field(default=None, description="可选实体标识，用于上下文。")


class DatabaseTableColumnsRequest(BaseModel):
    """查询指定表字段结构的请求。"""

    workspace_root: Optional[str] = Field(
        default=None,
        description="应用工作区根目录；缺失时返回连接失败状态。",
    )
    table_name: str = Field(default="", description="要读取字段结构的目标表名。")


def database_tables(request: DatabaseTablesRequest) -> dict[str, Any]:
    """返回当前数据库的表清单（名称/注释），失败时给出可展示的错误状态。"""

    schema_context = inspect_mysql_schema(
        {
            "entity_id": str(request.entity_id or ""),
            "data_source_id": "database_tool",
        },
        workspace_root=request.workspace_root,
    )
    if schema_context.get("status") != "completed":
        return {
            "tool": "database.tables",
            "status": "error",
            "database": "",
            "tables": [],
            "message": str(
                schema_context.get("message") or schema_context.get("summary") or "查询失败"
            ),
        }
    return {
        "tool": "database.tables",
        "status": "ok",
        "database": str(schema_context.get("database") or ""),
        "tables": [
            {
                "name": str(table.get("table_name") or table.get("name") or ""),
                "comment": str(table.get("comment") or ""),
            }
            for table in schema_context.get("tables") or []
            if table.get("table_name")
        ],
        "message": str(schema_context.get("summary") or ""),
    }


def database_table_columns(request: DatabaseTableColumnsRequest) -> dict[str, Any]:
    """返回指定表的字段结构（列名/类型/可空/说明），失败时给出可展示错误。"""

    table_name = str(request.table_name or "").strip()
    if not table_name:
        return {
            "tool": "database.table_columns",
            "status": "error",
            "table_name": "",
            "columns": [],
            "message": "缺少目标表名。",
        }
    schema_context = inspect_mysql_table(
        {
            "data_source_id": "database_tool",
        },
        table_name,
        workspace_root=request.workspace_root,
    )
    if schema_context.get("status") != "completed":
        return {
            "tool": "database.table_columns",
            "status": "error",
            "table_name": table_name,
            "columns": [],
            "message": str(
                schema_context.get("message") or schema_context.get("summary") or "查询失败"
            ),
        }
    columns: list[dict[str, Any]] = []
    for table in schema_context.get("tables") or []:
        if str(table.get("table_name") or table.get("name") or "") == table_name:
            columns = [
                {
                    "name": str(column.get("name") or ""),
                    "type": str(column.get("type") or ""),
                    "nullable": bool(column.get("nullable")),
                    "comment": str(column.get("comment") or ""),
                }
                for column in table.get("columns") or []
                if column.get("name")
            ]
            break
    return {
        "tool": "database.table_columns",
        "status": "ok" if columns else "error",
        "table_name": table_name,
        "columns": columns,
        "message": "" if columns else "未读取到该表的字段信息。",
    }
