"""Direct 实体建表 SQL 的生成、隔离校验与显式确认。"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.services.artifact_invalidation import canonical_sha256
from app.services.entity_definitions import ENTITY_FIELD_TYPES, entity_table_name
from app.topologies.queries import serves_agent_runtime_public_edge


_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_SQL_TYPES = {
    "text": "TEXT", "long_text": "TEXT", "number": "INTEGER",
    "decimal": "REAL", "date": "TEXT", "datetime": "TEXT",
    "enum": "TEXT", "boolean": "INTEGER",
}


class DirectEntityRequest(BaseModel):
    """约束 Direct 实体操作只能选中一个正式实体。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    entity_id: str = Field(alias="entityId", min_length=1, max_length=128)


class DirectEntityConfirmRequest(DirectEntityRequest):
    """确认前精确复验用户预览的 SQL 内容摘要。"""

    sql_sha256: str = Field(alias="sqlSha256", pattern=r"^[0-9a-f]{64}$")


def _read_entity(request: DirectEntityRequest) -> tuple[Path, dict[str, Any], dict[str, Any], str]:
    """读取已确认 Direct TechnicalPlan，按稳定 ID 唯一定位实体。"""

    root = Path(request.workspace_root).expanduser().resolve()
    plan_path = root / ".xcodeagent/plans/technical-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or plan.get("confirmation_status") != "confirmed":
        raise ValueError("TechnicalPlan 尚未确认。")
    if not serves_agent_runtime_public_edge(plan):
        raise ValueError("当前应用不是 agent_runtime_direct 拓扑。")
    matches = [
        entity for entity in plan.get("entities", [])
        if isinstance(entity, dict) and entity.get("id") == request.entity_id
    ]
    if len(matches) != 1:
        raise ValueError(f"TechnicalPlan 无法唯一定位实体 {request.entity_id}。")
    if not _IDENTIFIER.fullmatch(request.entity_id):
        raise ValueError("实体 ID 不符合 Direct SQL 文件身份规则。")
    return root, plan, matches[0], canonical_sha256(plan_path)


def _quoted(identifier: str) -> str:
    """只允许正式字段名并返回 SQLite 安全引用。"""

    if not _IDENTIFIER.fullmatch(identifier):
        raise ValueError(f"实体字段名非法：{identifier}。")
    return f'"{identifier}"'


def _entity_sql(entity: dict[str, Any]) -> tuple[str, str, list[str]]:
    """从正式实体字段确定性编译 SQLite DDL，不推断外部数据源。"""

    table = entity_table_name(str(entity["id"]))
    fields = entity.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError("实体缺少可生成建表 SQL 的正式字段。")
    columns = ['"id" TEXT PRIMARY KEY', '"owner_id" TEXT NOT NULL']
    field_names: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError("实体字段必须是对象。")
        name = str(field.get("name") or "").strip()
        field_type = str(field.get("type") or "").strip()
        _quoted(name)
        if name in {"id", "owner_id"} or name in field_names:
            raise ValueError(f"实体字段 {name} 与保留列冲突或重复。")
        if field_type not in ENTITY_FIELD_TYPES:
            raise ValueError(f"实体字段 {name} 的类型 {field_type} 不受 Direct SQLite 支持。")
        column = f"{_quoted(name)} {_SQL_TYPES[field_type]}"
        if field.get("required") is True:
            column += " NOT NULL"
        if field_type == "boolean":
            column += f" CHECK ({_quoted(name)} IN (0, 1))"
        columns.append(column)
        field_names.append(name)
    sql = f'CREATE TABLE IF NOT EXISTS {_quoted(table)} (\n  ' + ",\n  ".join(columns) + "\n);\n"
    return table, sql, field_names


def _check_sql(table: str, sql: str, fields: list[str]) -> None:
    """仅在隔离的内存库执行 DDL，并核对实际生成的列。"""

    with sqlite3.connect(":memory:") as connection:
        connection.executescript(sql)
        actual = [row[1] for row in connection.execute(f"PRAGMA table_info({_quoted(table)})")]
    expected = ["id", "owner_id", *fields]
    if actual != expected:
        raise ValueError("SQL 执行后的列与 TechnicalPlan 实体字段不一致。")


def _paths(root: Path, entity_id: str, sql: str) -> tuple[Path, Path, Path]:
    """为实体设计与生成项目返回不冲突的稳定文件路径。"""

    stem = entity_table_name(entity_id)
    digest = sha256(sql.encode("utf-8")).hexdigest()[:12]
    design_root = root / ".xcodeagent/plans/direct/entities"
    sql_root = root / "agent-runtime/src/app/infrastructure/migrations/sql"
    return (
        design_root / f"{stem}.json",
        design_root / f"{stem}.md",
        sql_root / f"001_entity_{stem}_{digest}.sql",
    )


def read_direct_entity_design(request: DirectEntityRequest) -> dict[str, Any]:
    """返回实体 SQL 候选及当前确认状态，过期证据不会计为完成。"""

    root, _plan, entity, plan_sha = _read_entity(request)
    table, sql, fields = _entity_sql(entity)
    _check_sql(table, sql, fields)
    sql_sha = sha256(sql.encode("utf-8")).hexdigest()
    json_path, markdown_path, sql_path = _paths(root, request.entity_id, sql)
    saved: dict[str, Any] | None = None
    if json_path.is_file() and markdown_path.is_file() and sql_path.is_file():
        try:
            value = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                saved = value
        except (OSError, UnicodeError, json.JSONDecodeError):
            saved = None
    confirmed = bool(
        saved
        and saved.get("entityId") == request.entity_id
        and saved.get("technicalPlanSha256") == plan_sha
        and saved.get("sqlSha256") == sql_sha
        and saved.get("status") == "confirmed"
        and sql_path.read_text(encoding="utf-8") == sql
        and markdown_path.read_text(encoding="utf-8").strip()
    )
    return {
        "entityId": request.entity_id,
        "tableName": table,
        "sql": sql,
        "sqlSha256": sql_sha,
        "technicalPlanSha256": plan_sha,
        "status": "confirmed" if confirmed else "pending",
        "sqlPath": str(sql_path.relative_to(root)),
        "fields": fields,
    }


def _publish_text(path: Path, content: str) -> None:
    """在同目录写临时文件并原子替换一个正式文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def confirm_direct_entity_design(request: DirectEntityConfirmRequest) -> dict[str, Any]:
    """只在预览摘要仍匹配时保存 SQL、Markdown 和确认事实。"""

    prepared = read_direct_entity_design(request)
    if prepared["sqlSha256"] != request.sql_sha256:
        raise ValueError("实体字段或 SQL 已变化，请重新预览后确认。")
    root = Path(request.workspace_root).expanduser().resolve()
    json_path, markdown_path, sql_path = _paths(root, request.entity_id, prepared["sql"])
    existing_versions = [
        path for path in sql_path.parent.glob(f"001_entity_{entity_table_name(request.entity_id)}_*.sql")
        if path != sql_path
    ]
    if existing_versions:
        raise ValueError("此实体已有不同版本的建表 SQL；不能用第二条 CREATE TABLE 伪装变更。请走正式修订并设计增量迁移。")
    if sql_path.is_file() and sql_path.read_text(encoding="utf-8") != prepared["sql"]:
        raise ValueError("同名迁移文件已存在不同内容，不能覆盖。")
    payload = {
        "artifactType": "direct-entity-sql",
        "status": "confirmed",
        "entityId": request.entity_id,
        "tableName": prepared["tableName"],
        "technicalPlanSha256": prepared["technicalPlanSha256"],
        "sqlSha256": prepared["sqlSha256"],
        "sqlPath": prepared["sqlPath"],
    }
    markdown = (
        f"# 实体 {request.entity_id} 建表 SQL\n\n"
        f"- 数据表：`{prepared['tableName']}`\n"
        f"- TechnicalPlan SHA-256：`{prepared['technicalPlanSha256']}`\n"
        f"- SQL SHA-256：`{prepared['sqlSha256']}`\n\n"
        f"```sql\n{prepared['sql']}```\n"
    )
    _publish_text(sql_path, prepared["sql"])
    _publish_text(markdown_path, markdown)
    _publish_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return read_direct_entity_design(request)
