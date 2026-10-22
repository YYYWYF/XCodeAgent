"""仅为映射提示读取当前选定表的数据库原始 DDL，不生成或执行建表语句。"""

from pathlib import Path
from typing import Any

import pymysql

from app.services.data_sources import database_source_mode, resolve_database_config


def load_mapping_table_ddl(workspace: str | Path, selection: dict[str, Any]) -> str | None:
    """直连和 DBID 读取原始 DDL；内置来源暂由提示词沿用字段结构投影。"""
    source_id = selection["sourceId"]
    if database_source_mode(workspace, source_id) == "builtin":
        return None
    config = resolve_database_config(workspace, source_id)
    if config["database"] != selection["schema"]:
        raise ValueError("数据库 Schema 已变化，请同步当前数据源后重新生成映射。")
    # 这里只引用查询目标标识符，建表定义完全使用数据库返回值。
    schema = selection["schema"].replace("`", "``")
    table = selection["table"].replace("`", "``")
    try:
        with pymysql.connect(
            host=config["host"], port=config["port"], user=config["user"], password=config["password"],
            database=config["database"], charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10, read_timeout=30, write_timeout=30,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SHOW CREATE TABLE `{schema}`.`{table}`")
                row = cursor.fetchone()
    except pymysql.MySQLError:
        raise ValueError("读取当前表的原始 DDL 失败，请检查数据库连接和 SHOW CREATE TABLE 权限。") from None
    ddl = row.get("Create Table") if isinstance(row, dict) else None
    if not isinstance(ddl, str) or not ddl.strip():
        raise ValueError("数据库未返回当前表的原始建表语句，无法生成映射。")
    return ddl
