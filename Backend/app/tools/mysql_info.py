from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from langchain_core.tools import tool

from app.services.database_credentials import (
    build_mysql_jdbc_url,
    DatabaseCredentialError,
    MySQLConnectionConfig,
    resolve_application_mysql_config,
)


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

@tool("get_mysql_config")
def get_mysql_config() -> str:
    """兼容旧工具入口，但没有绑定应用工作区时拒绝返回数据库配置。

    当后端项目 application.yml 缺少 spring.datasource 配置时，调用本工具获取
    数据库连接信息（host/port/user/password/database）及其 JDBC URL，据此填充
    application.yml 的 spring.datasource.url / username / password。

    Returns:
        A JSON string with the MySQL connection config.
    """

    return get_mysql_config_for_workspace(None)


def create_get_mysql_config_tool(workspace_root: str | None):
    """创建绑定当前应用工作区的数据源配置工具。"""

    @tool("get_mysql_config")
    def workspace_get_mysql_config() -> str:
        """读取当前应用的 MySQL 配置，用于补齐生成项目的数据源。"""

        return get_mysql_config_for_workspace(workspace_root)

    return workspace_get_mysql_config


def get_mysql_config_for_workspace(workspace_root: str | None) -> str:
    """解析当前应用配置并返回生成 Spring 数据源所需的连接信息。"""

    try:
        config = resolve_application_mysql_config(workspace_root)
    except DatabaseCredentialError as exc:
        return json.dumps(
            {
                "tool": "get_mysql_config",
                "status": "error",
                "error": str(exc),
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "tool": "get_mysql_config",
            "status": "ok",
            "host": config.host,
            "port": config.port,
            "user": config.user,
            "password": config.password,
            "database": config.database,
            "jdbc_url": build_mysql_jdbc_url(config),
        },
        ensure_ascii=False,
    )


@tool("get_mysql_table_info")
def get_mysql_table_info(
        table_name: str | None = None
) -> str:
    """从已绑定的当前应用工作区读取连接信息并检查 MySQL 数据库结构。

    Args:
        table_name: If provided, returns detail schema for this table only.
                    Otherwise returns all tables with column details.

    Returns:
        A JSON string with the database schema information.
    """

    return get_mysql_table_info_for_workspace(None, table_name=table_name)


def create_get_mysql_table_info_tool(workspace_root: str | None):
    """创建绑定应用工作区、但只向模型暴露 table_name 的 MySQL 工具。"""

    @tool("get_mysql_table_info")
    def workspace_get_mysql_table_info(table_name: str | None = None) -> str:
        """检查当前应用配置的 MySQL 数据库结构。"""

        return get_mysql_table_info_for_workspace(
            workspace_root,
            table_name=table_name,
        )

    return workspace_get_mysql_table_info


def get_mysql_table_info_for_workspace(
    workspace_root: str | None,
    *,
    table_name: str | None = None,
) -> str:
    """在确定性后端边界解析凭据并调用原有 mysql_table_info。"""

    try:
        config = resolve_application_mysql_config(workspace_root)
    except DatabaseCredentialError as exc:
        return json.dumps(
            {
                "tool": "get_mysql_table_info",
                "status": "error",
                "error": str(exc),
            },
            ensure_ascii=False,
        )
    return mysql_table_info(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        table_name=table_name,
    )


@tool("execute_mysql_ddl")
def execute_mysql_ddl(
        statements: list[str],
) -> str:
    """兼容旧工具入口，但没有绑定应用工作区时拒绝执行 DDL。

    Args:
        statements: 需要按顺序执行的 SQL 语句列表。每条语句以字符串传入，
                    不要带末尾分号。

    Returns:
        A JSON string with the execution result.
    """

    return execute_mysql_ddl_for_workspace(None, statements)


def create_execute_mysql_ddl_tool(workspace_root: str | None):
    """创建绑定当前应用工作区的 DDL 工具，避免向模型暴露连接凭据。"""

    @tool("execute_mysql_ddl")
    def workspace_execute_mysql_ddl(statements: list[str]) -> str:
        """按当前应用配置执行传入的 DDL/SQL 语句。"""

        return execute_mysql_ddl_for_workspace(workspace_root, statements)

    return workspace_execute_mysql_ddl


def execute_mysql_ddl_for_workspace(
    workspace_root: str | None,
    statements: list[str],
) -> str:
    """解析当前应用的加密数据库配置，并执行 DDL/SQL 语句。"""

    try:
        config = resolve_application_mysql_config(workspace_root)
    except DatabaseCredentialError as exc:
        return json.dumps(
            {
                "tool": "execute_mysql_ddl",
                "status": "error",
                "error": str(exc),
            },
            ensure_ascii=False,
        )
    return _execute_mysql_ddl_with_config(statements, config)


def _execute_mysql_ddl_with_config(
    statements: list[str],
    config: MySQLConnectionConfig,
) -> str:
    """使用已解析的应用级连接配置执行 SQL，并保留旧工具结果协议。"""

    import pymysql

    executed: list[dict[str, Any]] = []
    try:
        connection = pymysql.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            connect_timeout=10,
            read_timeout=30,
        )
    except pymysql.MySQLError as exc:
        return json.dumps(
            {
                "tool": "execute_mysql_ddl",
                "status": "error",
                "error": f"Connection failed: {exc}",
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps(
            {
                "tool": "execute_mysql_ddl",
                "status": "error",
                "error": f"Unexpected connection error: {exc}",
            },
            ensure_ascii=False,
        )

    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute("USE `" + config.database.replace("`", "``") + "`")
                for statement in statements:
                    statement = str(statement).strip().rstrip(";")
                    if not statement:
                        continue
                    cursor.execute(statement)
                    executed.append(
                        {
                            "statement_hash": sha256(
                                statement.encode("utf-8")
                            ).hexdigest(),
                            "rowcount": cursor.rowcount,
                        }
                    )
            connection.commit()
    except pymysql.MySQLError as exc:
        return json.dumps(
            {
                "tool": "execute_mysql_ddl",
                "status": "error",
                "error": f"Execution failed: {exc}",
                "executed_statements": executed,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps(
            {
                "tool": "execute_mysql_ddl",
                "status": "error",
                "error": f"Unexpected error: {exc}",
                "executed_statements": executed,
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "tool": "execute_mysql_ddl",
            "status": "completed",
            "summary": f"已执行 {len(executed)} 条 SQL 语句。",
            "executed_statements": executed,
        },
        ensure_ascii=False,
    )
