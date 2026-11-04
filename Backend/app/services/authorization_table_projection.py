"""权限 SQL 的有界词法读取与仅表名投影，不改变模板结构。"""

import hashlib
import json
import re
from pathlib import Path

from app.services.authorization_bootstrap_policy import read_authorization_ddl

TABLES = ("role", "resource", "role_resource", "role_member")
# 字符串和注释作为整体保留，分号及 SQL 关键字只在非注释区域识别。
_TOKEN = re.compile(r"\s+|--[^\n]*(?:\n|$)|\#[^\n]*(?:\n|$)|/\*[\s\S]*?\*/|'(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"|`[^`]+`|[A-Za-z_0-9]+|[^\s]", re.M)


def sql_tokens(text: str) -> list[tuple[str, int, int]]:
    """返回有效词法锚点及原始位置，忽略注释但拒绝可执行注释。"""

    result = []
    for match in _TOKEN.finditer(text):
        token = match.group()
        if token.startswith(("/*!", "/*+")):
            raise ValueError("权限 SQL 不支持可执行注释。")
        if token.isspace() or token.startswith(("--", "#", "/*")):
            continue
        result.append((token, match.start(), match.end()))
    return result


def table_mapping(key: str) -> dict[str, str]:
    """仅接受当前注册合同的十二位小写十六进制应用前缀。"""

    if not re.fullmatch(r"[0-9a-f]{12}", key):
        raise ValueError("模拟数据库 datasourceKey 无效。")
    return {name: f"{key}_{name}" for name in TABLES}


def fingerprint(value: object) -> str:
    """对完整不可变输入生成稳定审计摘要。"""

    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def compile_authorization_tables(root: Path, key: str) -> dict:
    """严格读取四条独立 CREATE，只替换四个头部标识符并复核关系表主键。"""

    mapping = table_mapping(key)
    source = read_authorization_ddl(root)
    if not source.get("ddl_available"):
        raise ValueError(source.get("ddl_error"))
    ddl = source["ddl"]
    tokens = sql_tokens(ddl)
    statements, start, group = [], 0, []
    for token in tokens:
        group.append(token)
        if token[0] == ";":
            statements.append((ddl[start:token[2]], group))
            start, group = token[2], []
    if group or len(statements) != 4:
        raise ValueError("权限 DDL 必须恰好包含四条以分号结束的 CREATE TABLE。")
    # 最后一条语句后的原始注释和空白也保留，反向还原不能悄悄丢弃模板字节。
    last_sql, last_tokens = statements[-1]
    statements[-1] = (last_sql + ddl[start:], last_tokens)
    result = {}
    offset = 0
    for statement, items in statements:
        words = [item[0].upper() for item in items]
        if words[:2] != ["CREATE", "TABLE"]:
            raise ValueError("权限 DDL 不允许额外语句。")
        index = 5 if words[2:5] == ["IF", "NOT", "EXISTS"] else 2
        name = items[index][0].strip("`")
        if name not in mapping or name in result or words[index + 1] != "(":
            raise ValueError("权限 DDL 表名必须与四表白名单一一对应。")
        # 用括号深度识别顶层列和约束，避免注释、默认字符串伪造主键。
        depth, segments, segment = 0, [], []
        for word in words[index + 1:-1]:
            if word == "(":
                depth += 1
            elif word == ")":
                depth -= 1
            if depth == 1 and word == ",":
                segments.append(segment)
                segment = []
            elif depth >= 1:
                segment.append(word)
        segments.append(segment)
        if depth != 0:
            raise ValueError("权限 DDL 括号结构无效。")
        normalized = [[word.strip("`") for word in part if word != "("] for part in segments]
        if name in {"role_resource", "role_member"}:
            ids = [part for part in normalized if part and part[0] == "ID"]
            primary = any(part == ["PRIMARY", "KEY", "ID", ")"] for part in normalized)
            if len(ids) != 1 or "AUTO_INCREMENT" not in ids[0] or not (primary or "PRIMARY" in ids[0] and "KEY" in ids[0]):
                raise ValueError("关系权限表必须由模板提供显式 id 自增主键。")
        original, begin, end = items[index]
        replacement = f"`{mapping[name]}`" if original.startswith("`") else mapping[name]
        begin, end = begin - offset, end - offset
        projected = statement[:begin] + replacement + statement[end:]
        if projected[:begin] + original + projected[begin + len(replacement):] != statement:
            raise ValueError("权限 DDL 反向校验失败。")
        result[name] = projected
        offset += len(statement)
    sqls = [result[name] for name in TABLES]
    return {"sqls": sqls, "tables": list(mapping.values()), "ddlFingerprint": fingerprint(sqls)}
