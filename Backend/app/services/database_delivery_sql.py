"""比较两个已确认结构，生成版本升级 SQL，不读取数据库或推断字段重命名。"""

from app.domain.database_delivery import DeliveryTable
from app.services.entity_table_sql import column_sql, create_table_sql, sql_text


def full_schema_sql(tables: list[DeliveryTable]) -> str:
    """将当前全部实体按表名稳定排序，输出可用于空库初始化的脚本。"""

    statements = [create_table_sql(table.model_dump()) for table in sorted(tables, key=lambda table: table.name)]
    return "\n\n".join(statements) + ("\n" if statements else "")


def _table_changes(before: DeliveryTable, after: DeliveryTable) -> list[str]:
    """逐列比较结构与顺序，字段名变化按删除加新增处理，不猜测数据映射。"""

    statements: list[str] = []
    old_columns = {column.name: column for column in before.columns}
    wanted = {column.name for column in after.columns}
    order = [column.name for column in before.columns if column.name in wanted]
    for name in sorted(old_columns.keys() - wanted):
        statements.append(f"-- 注意：删除字段 `{name}` 会丢失该字段数据。\nALTER TABLE `{after.name}` DROP COLUMN `{name}`;")
    for index, column in enumerate(after.columns):
        position = f"AFTER `{after.columns[index - 1].name}`" if index else "FIRST"
        definition = column_sql(column.model_dump())
        if column.name not in old_columns:
            statements.append(f"ALTER TABLE `{after.name}` ADD COLUMN {definition} {position};")
            order.insert(index, column.name)
        elif old_columns[column.name] != column or order.index(column.name) != index:
            statements.append(f"ALTER TABLE `{after.name}` MODIFY COLUMN {definition} {position};")
            order.remove(column.name)
            order.insert(index, column.name)
    if before.comment != after.comment:
        statements.append(f"ALTER TABLE `{after.name}` COMMENT='{sql_text(after.comment)}';")
    return statements


def incremental_schema_sql(before: list[DeliveryTable], after: list[DeliveryTable]) -> str:
    """生成上一交付版本到当前版本的变更，空差异返回真正的空文件内容。"""

    old = {table.name: table for table in before}
    current = {table.name: table for table in after}
    statements: list[str] = []
    for name in sorted(old.keys() - current.keys()):
        statements.append(f"-- 注意：删除表 `{name}` 会丢失全部数据。\nDROP TABLE `{name}`;")
    for name in sorted(current):
        if name not in old:
            statements.append(create_table_sql(current[name].model_dump()))
        else:
            statements.extend(_table_changes(old[name], current[name]))
    if not statements:
        return ""
    header = ("-- 从上一交付版本升级使用；执行前核对目标版本、备份数据，并评估删表、删列及类型变更。\n"
              "-- 新增非空字段需评估存量数据回填；字段或表改名按删除加新增表达，不推断数据搬迁。\n")
    return header + "\n\n".join(statements) + "\n"
