from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.tools import tool


def _escape(value: Any) -> Any:
    """Coerce DB-API types to JSON-serializable Python types."""
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
    """Connect to a MySQL database and read table schema information.

    Use this tool to inspect database structure for code generation or
    schema analysis. Returns table list (name + comment) and column
    definitions (name, type, nullable, default, key, comment) as JSON.

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
        """统一错误响应格式"""
        return json.dumps(
            {"tool": "mysql_table_info", "status": "error", "error": message},
            ensure_ascii=False,
        )

    # 1. 参数校验（提前失败）
    if not database:
        return _error_response("Database name is required.")
    # 2. 连接数据库
    try:
        connection = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
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
                # 获取所有表
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

                # 确定目标表
                if table_name:
                    if not any(t["table_name"] == table_name for t in tables):
                        return _error_response(
                            f"Table '{table_name}' not found in database '{database}'."
                        )
                    target_tables = [table_name]
                else:
                    target_tables = [t["table_name"] for t in tables]

                # 批量查询列信息（优化：单次查询所有表）
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

                    # 按表名分组
                    schemas = {}
                    for col in columns:
                        table = col.pop("table_name")
                        schemas.setdefault(table, []).append(col)
                else:
                    schemas = {}

                # 4. 构建响应
                result = {
                    "tool": "mysql_table_info",
                    "status": "ok",
                    "database": database,
                    "tables": tables,
                    "schemas": schemas,
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
    """Connect to a MySQL database and read table schema information.

    Use this tool to inspect database structure for code generation or
    schema analysis. Returns table list (name + comment) and column
    definitions (name, type, nullable, default, key, comment) as JSON.

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

    # 全部参数必填校验
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
