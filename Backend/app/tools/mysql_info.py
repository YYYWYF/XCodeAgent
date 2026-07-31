from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.tools import tool


def _escape(value: Any) -> Any:
    """把 DB-API 类型转换为 JSON 可序列化值。"""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "__float__"):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return str(value)


def _row_to_dict(row: Any, cursor: Any) -> dict[str, Any]:
    """把游标行转换成普通字典，兼容不同 DB-API 行对象。"""

    result = {}
    for desc in cursor.description:
        name = desc[0]
        if isinstance(row, dict):
            value = row.get(name)
        else:
            value = getattr(row, name, None)
        result[name] = _escape(value)
    return result


def mysql_table_info(
        host: str = "localhost",
        port: int = 3306,
        user: str = "",
        password: str = "",
        database: str = "",
        table_name: str | None = None
) -> str:
    """连接 MySQL Server 并读取目标数据库的事实结构。

    Args:
        host: MySQL server host address.
        port: MySQL server port number.
        user: MySQL username with read access to information_schema.
        password: MySQL password for the user.
        database: Name of the database to inspect.
        table_name: If provided, returns detail schema for this table only.
                    Otherwise returns all tables with column details.

    Returns:
        A JSON string with the database schema information.
    """
    import pymysql

    def _error_response(message: str) -> str:
        """构造只表示连接或元数据访问失败的错误响应。"""

        return json.dumps(
            {"tool": "mysql_table_info", "status": "error", "error": message},
            ensure_ascii=False,
        )

    if not database:
        return _error_response("Database name is required.")
    try:
        connection = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            read_timeout=30,
        )
    except pymysql.MySQLError as exc:
        return _error_response(f"Connection failed: {exc}")
    except Exception as exc:
        return _error_response(f"Unexpected connection error: {exc}")
    # 3. 查询数据（使用上下文管理器确保连接关闭）
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT SCHEMA_NAME AS database_name
                    FROM information_schema.SCHEMATA
                    WHERE SCHEMA_NAME = %s
                    """,
                    (database,),
                )
                database_row = cursor.fetchone()
                if not database_row:
                    return json.dumps(
                        {
                            "tool": "mysql_table_info",
                            "status": "ok",
                            "database": database,
                            "database_exists": False,
                            "tables": [],
                            "schemas": {},
                            "indexes": {},
                            "foreign_keys": {},
                        },
                        ensure_ascii=False,
                        default=str,
                    )

                cursor.execute(
                    """
                    SELECT TABLE_NAME AS table_name,
                           TABLE_COMMENT AS comment
                    FROM information_schema.TABLES
                    WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
                    ORDER BY TABLE_NAME
                    """,
                    (database,),
                )
                tables = cursor.fetchall()  # DictCursor 已返回字典

                if table_name:
                    table_name = table_name.lower()
                    target_tables = [
                        table_name
                    ] if any(t["table_name"] == table_name for t in tables) else []
                else:
                    target_tables = [t["table_name"] for t in tables]

                if target_tables:
                    placeholders = ",".join(["%s"] * len(target_tables))
                    cursor.execute(
                        f"""
                        SELECT
                            TABLE_NAME AS table_name,
                            COLUMN_NAME AS column_name,
                            COLUMN_TYPE AS column_type,
                            IS_NULLABLE AS is_nullable,
                            COLUMN_DEFAULT AS column_default,
                            COLUMN_KEY AS column_key,
                            EXTRA AS extra,
                            COLUMN_COMMENT AS comment
                        FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA = %s 
                          AND TABLE_NAME IN ({placeholders})
                        ORDER BY TABLE_NAME, ORDINAL_POSITION
                        """,
                        (database, *target_tables),
                    )
                    columns = cursor.fetchall()

                    schemas = {}
                    for col in columns:
                        table = col.pop("table_name")
                        schemas.setdefault(table, []).append(col)

                    cursor.execute(
                        f"""
                        SELECT
                            TABLE_NAME AS table_name,
                            INDEX_NAME AS index_name,
                            NON_UNIQUE AS non_unique,
                            COLUMN_NAME AS column_name,
                            SEQ_IN_INDEX AS seq_in_index
                        FROM information_schema.STATISTICS
                        WHERE TABLE_SCHEMA = %s
                          AND TABLE_NAME IN ({placeholders})
                        ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
                        """,
                        (database, *target_tables),
                    )
                    index_rows = cursor.fetchall()
                    indexes: dict[str, dict[str, Any]] = {}
                    for row in index_rows:
                        table = row.get("table_name")
                        name = row.get("index_name")
                        if not table or not name:
                            continue
                        index = indexes.setdefault(
                            table,
                            {},
                        ).setdefault(
                            name,
                            {
                                "name": name,
                                "unique": row.get("non_unique") == 0,
                                "columns": [],
                            },
                        )
                        index["columns"].append(row.get("column_name"))

                    cursor.execute(
                        f"""
                        SELECT
                            kcu.TABLE_NAME AS table_name,
                            kcu.CONSTRAINT_NAME AS constraint_name,
                            kcu.COLUMN_NAME AS column_name,
                            kcu.REFERENCED_TABLE_NAME AS referenced_table,
                            kcu.REFERENCED_COLUMN_NAME AS referenced_column
                        FROM information_schema.KEY_COLUMN_USAGE kcu
                        WHERE kcu.TABLE_SCHEMA = %s
                          AND kcu.TABLE_NAME IN ({placeholders})
                          AND kcu.REFERENCED_TABLE_NAME IS NOT NULL
                        ORDER BY kcu.TABLE_NAME, kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION
                        """,
                        (database, *target_tables),
                    )
                    fk_rows = cursor.fetchall()
                    foreign_keys: dict[str, list[dict[str, Any]]] = {}
                    for row in fk_rows:
                        table = row.get("table_name")
                        if not table:
                            continue
                        foreign_keys.setdefault(table, []).append(
                            {
                                "name": row.get("constraint_name"),
                                "column": row.get("column_name"),
                                "referenced_table": row.get("referenced_table"),
                                "referenced_column": row.get("referenced_column"),
                            }
                        )
                    indexes = {
                        table: list(items.values()) for table, items in indexes.items()
                    }
                else:
                    schemas = {}
                    indexes = {}
                    foreign_keys = {}

                result = {
                    "tool": "mysql_table_info",
                    "status": "ok",
                    "database": database,
                    "database_exists": True,
                    "tables": tables,
                    "schemas": schemas,
                    "indexes": indexes,
                    "foreign_keys": foreign_keys,
                }
                return json.dumps(result, ensure_ascii=False, default=str)

    except pymysql.MySQLError as exc:
        return _error_response(f"Query failed: {exc}")
    except Exception as exc:
        return _error_response(f"Unexpected error: {exc}")

@tool("get_mysql_table_info")
def get_mysql_table_info(
        table_name: str | None = None
) -> str:
    """从 MYSQL_* 环境变量读取连接信息并检查 MySQL 数据库结构。

    Args:
        table_name: If provided, returns detail schema for this table only.
                    Otherwise returns all tables with column details.

    Returns:
        A JSON string with the database schema information.
    """

    host = os.getenv("MYSQL_HOST")
    port_str = os.getenv("MYSQL_PORT")
    user = os.getenv("MYSQL_USER")
    password = os.getenv("MYSQL_PWD")
    database = os.getenv("MYSQL_DATABASE")

    missing = []
    if not host:
        missing.append("MYSQL_HOST")
    if not port_str:
        missing.append("MYSQL_PORT")
    if not user:
        missing.append("MYSQL_USER")
    if not password:
        missing.append("MYSQL_PWD")
    if not database:
        missing.append("MYSQL_DATABASE")
    if missing:
        return json.dumps(
            {
                "tool": "get_mysql_table_info",
                "status": "error",
                "error": (
                    "Missing required environment variable(s): "
                    f"{', '.join(missing)}. "
                    "All of MYSQL_HOST, MYSQL_PORT, MYSQL_USER, "
                    "MYSQL_PWD, MYSQL_DATABASE must be set."
                ),
            },
            ensure_ascii=False,
        )

    try:
        port = int(port_str)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return json.dumps(
            {
                "tool": "get_mysql_table_info",
                "status": "error",
                "error": f"MYSQL_PORT must be a valid integer, got '{port_str}'.",
            },
            ensure_ascii=False,
        )

    return mysql_table_info(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        table_name=table_name,
    )
