"""解析并校验实体 CREATE TABLE 的受控 MySQL 语法，不改写模型 SQL。"""

import re
from dataclasses import dataclass
from typing import Any


_IDENTIFIER = r"`(?:``|[^`\x00])+`"
_COMMENT = r"'(?:''|\\\\|[^'\\\x00])*'"
_TYPE = r"[A-Z]+(?:\s*\(\s*[0-9]+(?:\s*,\s*[0-9]+)?\s*\))?"
_CREATE = re.compile(rf"CREATE\s+TABLE\s+(?P<table>{_IDENTIFIER})\s*\(\s*", re.I)
_COLUMN = re.compile(
    rf"(?P<name>{_IDENTIFIER})\s+(?P<type>{_TYPE})\s+(?P<required>NOT\s+)?NULL"
    r"(?P<default>\s+DEFAULT\s+CURRENT_TIMESTAMP(?:\(\))?"
    r"(?:\s+ON\s+UPDATE\s+CURRENT_TIMESTAMP(?:\(\))?)?)?"
    rf"\s+COMMENT\s+(?P<comment>{_COMMENT})\s*",
    re.I,
)
_PRIMARY = re.compile(rf"PRIMARY\s+KEY\s*\(\s*(?P<column>{_IDENTIFIER})\s*\)\s*", re.I)
_INDEX = re.compile(
    rf"(?:KEY|INDEX)\s+(?P<index>{_IDENTIFIER})\s*\(\s*(?P<column>{_IDENTIFIER})\s*\)\s*", re.I
)
_OPTIONS = re.compile(
    r"\s*ENGINE\s*=\s*InnoDB\s+DEFAULT\s+(?:CHARSET|CHARACTER\s+SET)\s*=\s*utf8mb4"
    rf"\s+COLLATE\s*=\s*utf8mb4_bin\s+COMMENT\s*=\s*(?P<comment>{_COMMENT})\s*;?\s*",
    re.I,
)
_SEPARATOR = re.compile(r",\s*")
_FIELD_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
_MARKED_IDENTITY = re.compile(r"^唯一标识(?:符)?[。.]?$|[（(「“]唯一标识(?:符)?[）)」”]")
_VARCHAR = re.compile(r"VARCHAR\(([1-9][0-9]{0,4})\)")
_DECIMAL = re.compile(r"DECIMAL\(([1-9][0-9]?),([0-9]{1,2})\)")
_TYPES = {
    "text": _VARCHAR.pattern,
    "long_text": r"VARCHAR\(512\)",
    "enum": _VARCHAR.pattern,
    "number": r"INT|BIGINT",
    "decimal": _DECIMAL.pattern,
    "date": r"TIMESTAMP",
    "datetime": r"TIMESTAMP",
    "boolean": r"TINYINT\(1\)",
}


@dataclass(frozen=True)
class EntityDdlStructure:
    """保留列声明与表注释的原始片段，供交付增量比较使用。"""

    columns: dict[str, re.Match[str]]
    primary: str | None
    indexes: set[str]
    table_comment: str


def _identifier(quoted: str) -> str:
    """解码反引号标识符并比较原始规划名称。"""

    return quoted[1:-1].replace("``", "`")


def _validate_column(match: re.Match, field: dict[str, Any]) -> str:
    """核对列身份、语义类型、可空性以及允许的时间默认值语法。"""

    name = _identifier(match["name"])
    sql_type = re.sub(r"\s+", "", match["type"]).upper()
    if name != field["name"]:
        raise ValueError("AI 生成的 DDL 列名或顺序与已确认字段清单不一致。")
    if re.fullmatch(_TYPES[field["type"]], sql_type) is None:
        raise ValueError(f"AI 生成的字段 `{name}` 类型不符合已确认设计或 DDL 规则。")
    if size := _VARCHAR.fullmatch(sql_type):
        length = int(size[1])
        if length > 16383:
            raise ValueError(f"AI 生成的字段 `{name}` 字符长度超出 MySQL 限制。")
        if field["type"] == "enum" and any(len(value) > length for value in field.get("enum_values", [])):
            raise ValueError(f"字段 `{name}` 的 VARCHAR 长度不足以容纳已确认的枚举取值。")
    if precision := _DECIMAL.fullmatch(sql_type):
        total, scale = int(precision[1]), int(precision[2])
        if total > 65 or scale > min(30, total):
            raise ValueError(f"AI 生成的字段 `{name}` 小数精度超出 MySQL 限制。")
    if bool(match["required"]) != field["required"]:
        raise ValueError(f"AI 生成的字段 `{name}` 可空性与已确认设计不一致。")
    if match["default"] and sql_type != "TIMESTAMP":
        raise ValueError(f"字段 `{name}` 不能使用审计时间默认值。")
    return sql_type


def parse_entity_ddl(sql: str, table_name: str) -> EntityDdlStructure:
    """识别单条建表语句及其声明，不调整原始 SQL 的换行或注释。"""

    create = _CREATE.match(sql)
    if not create or _identifier(create["table"]) != table_name:
        raise ValueError("AI 必须为当前目标表生成一条 CREATE TABLE 语句。")

    columns: dict[str, re.Match[str]] = {}
    primary = None
    indexes: set[str] = set()
    cursor = create.end()
    # 按允许的声明消费 SQL，注释中的逗号、括号和分号不会被当作 SQL 分隔符。
    while True:
        if column := _COLUMN.match(sql, cursor):
            name = _identifier(column["name"])
            if not _FIELD_NAME.fullmatch(name) or name in columns:
                raise ValueError("AI 生成的 DDL 包含无效或重复字段。")
            columns[name] = column
            cursor = column.end()
        elif key := _PRIMARY.match(sql, cursor):
            if primary is not None:
                raise ValueError("AI 生成的 DDL 最多允许一个单列主键。")
            primary = _identifier(key["column"])
            cursor = key.end()
        elif index := _INDEX.match(sql, cursor):
            name = _identifier(index["index"])
            target = _identifier(index["column"])
            if name != f"idx_{target}" or len(name) > 64 or target in indexes:
                raise ValueError("AI 生成的普通索引名称无效或重复。")
            indexes.add(target)
            cursor = index.end()
        else:
            raise ValueError("AI 生成的 DDL 含有不支持的列定义或约束。")

        if sql[cursor:cursor + 1] == ")":
            break
        separator = _SEPARATOR.match(sql, cursor)
        if separator is None:
            raise ValueError("AI 生成的 DDL 建表定义不完整。")
        cursor = separator.end()

    options = _OPTIONS.fullmatch(sql, cursor + 1)
    if not options:
        raise ValueError("AI 生成的 DDL 表选项无效，或包含额外 SQL 语句。")
    if not columns or not indexes <= columns.keys() or (primary is not None and primary not in columns):
        raise ValueError("DDL 缺少字段或键引用了不存在的字段。")
    return EntityDdlStructure(columns, primary, indexes, options["comment"])


def validate_entity_ddl(sql: str, entity: dict[str, Any]) -> None:
    """将已解析的字段和键约束与当前已确认技术规划逐一核对。"""

    structure = parse_entity_ddl(sql, entity["table_name"])
    fields = entity["fields"]
    if len(structure.columns) > len(fields):
        raise ValueError("AI 生成的 DDL 包含未确认的实体字段。")
    if len(structure.columns) < len(fields):
        raise ValueError("AI 生成的 DDL 遗漏了已确认的实体字段。")
    column_types = {field["name"]: _validate_column(column, field)
                    for column, field in zip(structure.columns.values(), fields)}
    _validate_keys(fields, column_types, structure.primary, structure.indexes)


def _validate_keys(
    fields: list[dict[str, Any]], column_types: dict[str, str], primary: str | None, indexes: set[str],
) -> None:
    """核对主键依据、普通索引的目标区分度及字符索引长度。"""

    by_name = {field["name"]: field for field in fields}
    marked = {
        field["name"] for field in fields
        if any(_MARKED_IDENTITY.search(field.get(key, "")) for key in ("label", "description"))
    }
    if marked and {primary} != marked:
        raise ValueError("AI 生成的单列主键必须对应正式设计中标注为唯一标识的字段。")
    if primary is not None:
        field = by_name.get(primary)
        if not field or not field["required"] or not any(
            "唯一标识" in field.get(key, "") for key in ("label", "description")
        ):
            raise ValueError("AI 生成的单列主键缺少非空唯一标识依据。")
    if not indexes <= by_name.keys() or primary in indexes or len(indexes) > 64:
        raise ValueError("AI 生成的普通索引目标不存在、重复覆盖主键或超过数量限制。")
    # 布尔和二值枚举的取值范围可以从正式设计直接确定，其他字段由模型按业务语义判断。
    for field in fields:
        if field["name"] in indexes and (
            field["type"] == "boolean"
            or (field["type"] == "enum" and len(set(field.get("enum_values", []))) <= 2)
        ):
            raise ValueError(f"字段 `{field['name']}` 的取值区分度不足，不能创建普通索引。")
    for target in indexes | ({primary} if primary is not None else set()):
        if (size := _VARCHAR.fullmatch(column_types[target])) and int(size[1]) > 255:
            raise ValueError(f"字段 `{target}` 用作主键或普通索引时，VARCHAR 长度不得超过 255 个字符。")
