"""实体建表及版本交付共用的确定性 MySQL 编译器，不执行 SQL。"""

from typing import Any

from app.services.entity_definitions import (
    ENTITY_FIELD_NAME_RE, MYSQL_TYPE_BY_FIELD_TYPE, entity_mysql_target_table,
)


def sql_text(value: str) -> str:
    """转义 SQL 注释，避免引号、反斜杠或空字符越过字符串边界。"""

    return value.replace("\\", "\\\\").replace("'", "''").replace("\x00", "")


def column_sql(column: dict[str, Any]) -> str:
    """统一编译 CREATE 与 ALTER 的列定义，主键属性不在增量中丢失。"""

    nullable = "NULL" if column["nullable"] else "NOT NULL"
    auto = " AUTO_INCREMENT" if column.get("auto_increment") else ""
    return f"`{column['name']}` {column['type']} {nullable}{auto} COMMENT '{sql_text(column['comment'])}'"


def create_table_sql(table: dict[str, Any]) -> str:
    """输出与实体建表入口一致的完整 SQL，不使用 IF NOT EXISTS 掩盖结构差异。"""

    lines = ["  " + column_sql(column) for column in table["columns"]]
    lines.append("  PRIMARY KEY (`id`)")
    # 外置库的正式表名允许反引号，输出时按 MySQL 标识符规则转义。
    table_name = table["name"].replace("`", "``")
    return (f"CREATE TABLE `{table_name}` (\n" + ",\n".join(lines)
            + "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
            + f" COMMENT='{sql_text(table['comment'])}';")


def compile_entity_table(entity: dict[str, Any]) -> dict[str, Any]:
    """校验正式字段并生成目标表，显式 id 仍由统一的隐式主键承载。"""

    names: set[str] = set()
    for field in entity.get("fields", []):
        if not isinstance(field, dict):
            raise ValueError(f"实体 {entity.get('id')} 的字段定义必须是对象。")
        name = field.get("name", "")
        if not isinstance(name, str) or not ENTITY_FIELD_NAME_RE.fullmatch(name) or name in names:
            raise ValueError(f"实体 {entity.get('id')} 包含无效或重复字段 `{name}`，请先修订并确认技术规划。")
        if field.get("type") not in MYSQL_TYPE_BY_FIELD_TYPE:
            raise ValueError("实体包含不支持的字段类型，请先修订并确认技术规划。")
        names.add(name)
    table_entity = {**entity, "fields": [field for field in entity.get("fields", []) if field["name"] != "id"]}
    return entity_mysql_target_table(table_entity)
