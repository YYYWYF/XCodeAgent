"""比较两个已确认结构，生成版本升级 SQL，不读取数据库或推断字段重命名。"""

import re

from app.domain.database_delivery import AuthorizationDeliverySchema, DeliveredDatabase, DeliveryTable
from app.services.entity_ddl_validation import EntityDdlStructure, parse_entity_ddl


def _terminated_sql(sql: str) -> str:
    """只补齐脚本的语句分隔符，保留正式 CREATE TABLE 内容。"""

    return sql if sql.rstrip().endswith(";") else sql + ";"


def full_schema_sql(tables: list[DeliveryTable]) -> str:
    """将当前全部实体按表名稳定排序，输出可用于空库初始化的脚本。"""

    statements = [_terminated_sql(table.sql) for table in sorted(tables, key=lambda table: table.name)]
    return "\n\n".join(statements) + ("\n" if statements else "")


def _append_authorization_sql(business: str, schema: AuthorizationDeliverySchema | None) -> str:
    """只添加语句间隔，完整保留 SaaS 投影返回的每条 SQL 原文字节。"""

    if schema is None:
        return business
    authorization = "\n\n".join(schema.sqls)
    return business + ("\n" if business else "") + authorization


def full_delivery_sql(tables: list[DeliveryTable], authorization_schema: AuthorizationDeliverySchema | None) -> str:
    """权限关闭时逐字节沿用业务脚本，开启时在其后追加四表投影。"""

    return _append_authorization_sql(full_schema_sql(tables), authorization_schema)


def incremental_delivery_sql(previous: DeliveredDatabase | None, tables: list[DeliveryTable],
                             authorization_schema: AuthorizationDeliverySchema | None) -> str:
    """首版增量为空，后续只支持首次加入权限表，不猜测修改或删除迁移。"""

    if previous is None:
        return ""
    before = previous.authorizationSchema
    if before is not None and before != authorization_schema:
        raise ValueError("权限 DDL 已变化或权限已关闭，需要明确迁移；不会自动修改或删除权限表。")
    business = incremental_schema_sql(previous.tables, tables)
    return _append_authorization_sql(business, authorization_schema if before is None else None)


def _column_signature(column: re.Match[str]) -> tuple[str, bool, str, str]:
    """忽略类型和时间表达式的空格及大小写，保留注释原文供对比。"""

    sql_type = re.sub(r"\s+", "", column["type"]).upper()
    default = re.sub(r"[\s()]", "", column["default"] or "").upper()
    return sql_type, bool(column["required"]), default, column["comment"]


def _column_changes(before: EntityDdlStructure, after: EntityDdlStructure) -> list[str]:
    """按正式 DDL 对比列及顺序，增改列保留模型确认过的完整定义。"""

    order = [name for name in before.columns if name in after.columns]
    changes = [f"DROP COLUMN {before.columns[name]['name']}" for name in sorted(before.columns.keys() - after.columns.keys())]
    position = "FIRST"
    for index, (name, column) in enumerate(after.columns.items()):
        definition = column[0].rstrip()
        if name not in before.columns:
            changes.append(f"ADD COLUMN {definition} {position}")
            order.insert(index, name)
        elif _column_signature(before.columns[name]) != _column_signature(column) or order.index(name) != index:
            changes.append(f"MODIFY COLUMN {definition} {position}")
            order.remove(name)
            order.insert(index, name)
        position = f"AFTER {column['name']}"
    return changes


def _table_changes(before: DeliveryTable, after: DeliveryTable) -> list[str]:
    """从已交付和当前正式 DDL 生成单次表结构变更，保留主键和索引变更。"""

    old = parse_entity_ddl(before.sql, before.name)
    new = parse_entity_ddl(after.sql, after.name)
    if old.primary != new.primary and new.primary is not None:
        # 主键接管由 MySQL 按实际数据校验唯一性，脚本不会自行清理历史数据。
        warnings = [f"-- 注意：设为主键前，请核对 `{new.primary}` 没有空值或重复值。"]
    else:
        warnings = []
    warnings.extend(f"-- 注意：删除字段 {old.columns[name]['name']} 会丢失该字段数据。"
                    for name in sorted(old.columns.keys() - new.columns.keys()))
    # 键和列在同一条 ALTER 内切换，避免删除旧键后才发现新结构不可用。
    changes = [f"DROP INDEX `idx_{name}`" for name in sorted(old.indexes - new.indexes)]
    if old.primary is not None and old.primary != new.primary:
        changes.append("DROP PRIMARY KEY")
    changes.extend(_column_changes(old, new))
    if new.primary is not None and old.primary != new.primary:
        changes.append(f"ADD PRIMARY KEY ({new.columns[new.primary]['name']})")
    changes.extend(f"ADD KEY `idx_{name}` ({new.columns[name]['name']})"
                   for name in sorted(new.indexes - old.indexes))
    if old.table_comment != new.table_comment:
        changes.append(f"COMMENT={new.table_comment}")
    if not changes:
        return []
    return ["\n".join([*warnings, f"ALTER TABLE `{after.name}`\n  " + ",\n  ".join(changes) + ";"])]


def incremental_schema_sql(before: list[DeliveryTable], after: list[DeliveryTable]) -> str:
    """生成上一交付版本到当前版本的变更，空差异返回真正的空文件内容。"""

    old = {table.name: table for table in before}
    current = {table.name: table for table in after}
    statements: list[str] = []
    for name in sorted(old.keys() - current.keys()):
        statements.append(f"-- 注意：删除表 `{name}` 会丢失全部数据。\nDROP TABLE `{name}`;")
    for name in sorted(current):
        if name not in old:
            statements.append(_terminated_sql(current[name].sql))
        else:
            statements.extend(_table_changes(old[name], current[name]))
    if not statements:
        return ""
    header = ("-- 从上一交付版本升级使用；执行前核对目标版本、备份数据，并评估删表、删列及类型变更。\n"
              "-- 新增非空字段需评估存量数据回填；字段或表改名按删除加新增表达，不推断数据搬迁。\n")
    return header + "\n\n".join(statements) + "\n"
