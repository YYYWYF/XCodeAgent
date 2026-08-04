from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.middleware.approvals import approval_store, operation_fingerprint
from app.services.database_credentials import (
    DatabaseCredentialError,
    resolve_application_mysql_config,
)


_HIGH_RISK_PATTERNS = (
    r"\bdrop\s+table\b",
    r"\bdrop\s+database\b",
    r"\btruncate\s+table\b",
    r"\balter\s+table\b[\s\S]*\bdrop\s+(column\s+)?",
    r"\bdelete\s+from\b(?![\s\S]*\bwhere\b)",
    r"\bupdate\s+[\w`.\-]+\s+set\b(?![\s\S]*\bwhere\b)",
    r"\brename\s+table\b",
    r"\balter\s+table\b[\s\S]*\b(modify|change)\s+(column\s+)?",
)

_HIGH_RISK_TASK_KEYWORDS = (
    "删除字段",
    "删除列",
    "删除数据",
    "清空",
    "drop",
    "truncate",
    "delete data",
    "remove column",
)


@dataclass(frozen=True)
class DatabaseExecutionContext:
    """记录数据库执行前的真实结构快照和任务范围。"""

    schema_summary: dict[str, Any]
    schema_hash: str
    database: str


def create_database_execution_context(schema_summary: dict[str, Any]) -> DatabaseExecutionContext:
    """根据最新数据库摘要创建执行上下文，后续审批会绑定该摘要指纹。"""

    digest_payload = {
        "database": schema_summary.get("database"),
        "tables": schema_summary.get("tables") or [],
        "summary": schema_summary.get("summary"),
    }
    schema_hash = sha256(
        json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return DatabaseExecutionContext(
        schema_summary=schema_summary,
        schema_hash=schema_hash,
        database=str(schema_summary.get("database") or ""),
    )


def database_plan_hash(plan: dict[str, Any]) -> str:
    """生成数据库执行计划指纹，用于把审批绑定到不可变计划。"""

    return sha256(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def classify_database_plan_risk(
    *,
    tasks: list[dict[str, Any]],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """结合任务声明和 SQL 计划进行数据库风险分级。"""

    reasons: list[str] = []
    statements = _plan_statements(plan)
    combined_sql = "\n".join(statements).lower()
    task_text = json.dumps(tasks, ensure_ascii=False, default=str).lower()

    if any(str(task.get("risk") or "").lower() == "high" for task in tasks):
        reasons.append("任务规划已标记为 high 风险。")
    for pattern in _HIGH_RISK_PATTERNS:
        if re.search(pattern, combined_sql, flags=re.IGNORECASE):
            reasons.append(f"SQL 命中高危模式：{pattern}")
    for keyword in _HIGH_RISK_TASK_KEYWORDS:
        if keyword.lower() in task_text:
            reasons.append(f"任务描述包含高危关键词：{keyword}")

    return {
        "level": "high" if reasons else "low",
        "reasons": list(dict.fromkeys(reasons)),
    }


def request_database_approval_if_needed(
    *,
    tasks: list[dict[str, Any]],
    plan: dict[str, Any],
    risk: dict[str, Any],
    execution_context: DatabaseExecutionContext,
) -> dict[str, Any] | None:
    """高危数据库计划在执行前创建或消费审批；低风险直接放行。"""

    if risk.get("level") != "high":
        return None
    operation_payload = _approval_operation_payload(
        tasks=tasks,
        plan=plan,
        execution_context=execution_context,
    )
    operation_key = operation_fingerprint("database.execute", operation_payload)
    if approval_store.is_operation_approved(tool="database.execute", operation_key=operation_key):
        return None
    if approval_store.consume_approved_once(tool="database.execute", operation_key=operation_key):
        return None
    approval = approval_store.request(
        tool="database.execute",
        operation_key=operation_key,
        title="高危数据库操作审批",
        description="Database Agent 生成了高危数据库变更计划，需要用户批准后才能执行。",
        subject=f"{execution_context.database or '未指定数据库'} / {database_plan_hash(plan)[:12]}",
        risk=risk,
        details=json.dumps(
            {
                "plan_hash": database_plan_hash(plan),
                "schema_hash": execution_context.schema_hash,
                "statements": _plan_statements(plan),
                "tasks": [task.get("id") for task in tasks],
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    return approval


def execute_database_plan(
    *,
    plan: dict[str, Any],
    execution_context: DatabaseExecutionContext,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    """按当前应用配置执行已审批或低风险计划，并返回紧凑执行证据。"""

    statements = _plan_statements(plan)
    if not statements:
        return {
            "status": "skipped",
            "summary": "数据库计划没有可执行 SQL。",
            "executed_statements": [],
            "schema_hash": execution_context.schema_hash,
        }
    connection_config = _mysql_connection_config(workspace_root)
    if connection_config.get("status") == "error":
        return {**connection_config, "schema_hash": execution_context.schema_hash}
    if (
        execution_context.database
        and connection_config.get("database") != execution_context.database
    ):
        return {
            "status": "error",
            "failure_category": "tool_error",
            "failure_reason": "当前应用数据库配置已变化，请重新读取数据库结构后再执行。",
            "schema_hash": execution_context.schema_hash,
        }

    import pymysql

    executed: list[dict[str, Any]] = []
    try:
        connection = pymysql.connect(
            host=connection_config["host"],
            port=connection_config["port"],
            user=connection_config["user"],
            password=connection_config["password"],
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            connect_timeout=10,
            read_timeout=30,
            write_timeout=30,
        )
        with connection:
            with connection.cursor() as cursor:
                database = str(connection_config["database"] or "")
                if database and execution_context.schema_summary.get("database_exists") is False:
                    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {_quoted_identifier(database)}")
                    executed.append(
                        {
                            "statement_hash": sha256(
                                f"CREATE DATABASE IF NOT EXISTS {database}".encode("utf-8")
                            ).hexdigest(),
                            "rowcount": cursor.rowcount,
                        }
                    )
                if database:
                    cursor.execute(f"USE {_quoted_identifier(database)}")
                for statement in statements:
                    cursor.execute(statement)
                    executed.append(
                        {
                            "statement_hash": sha256(statement.encode("utf-8")).hexdigest(),
                            "rowcount": cursor.rowcount,
                        }
                    )
            connection.commit()
    except Exception as exc:
        return {
            "status": "failed",
            "failure_category": "tool_error",
            "failure_reason": f"数据库执行失败：{type(exc).__name__}: {exc}",
            "executed_statements": executed,
            "schema_hash": execution_context.schema_hash,
        }

    return {
        "status": "completed",
        "summary": f"已执行 {len(executed)} 条数据库语句；MySQL DDL 可能隐式提交。",
        "executed_statements": executed,
        "schema_hash": execution_context.schema_hash,
    }


def _quoted_identifier(value: str) -> str:
    """安全引用 MySQL 标识符，避免库名中的反引号破坏语句。"""

    return "`" + value.replace("`", "``") + "`"


def _approval_operation_payload(
    *,
    tasks: list[dict[str, Any]],
    plan: dict[str, Any],
    execution_context: DatabaseExecutionContext,
) -> dict[str, Any]:
    """构造审批绑定的不可变操作载荷。"""

    return {
        "task_ids": [str(task.get("id") or "") for task in tasks],
        "database": execution_context.database,
        "schema_hash": execution_context.schema_hash,
        "plan_hash": database_plan_hash(plan),
        "statements": _plan_statements(plan),
    }


def _plan_statements(plan: dict[str, Any]) -> list[str]:
    """从模型计划中读取 SQL 语句列表，并过滤空值。"""

    raw = plan.get("statements") or plan.get("sql") or []
    if isinstance(raw, str):
        raw = [raw]
    return [statement.strip().rstrip(";") for statement in raw if str(statement).strip()]


def _mysql_connection_config(workspace_root: str | Path | None) -> dict[str, Any]:
    """从当前应用工作区解析 MySQL 连接信息，不回退到全局环境变量。"""

    try:
        config = resolve_application_mysql_config(workspace_root)
    except DatabaseCredentialError as exc:
        return {
            "status": "error",
            "failure_category": "tool_error",
            "failure_reason": str(exc),
        }
    return {
        "host": config.host,
        "port": config.port,
        "user": config.user,
        "password": config.password,
        "database": config.database,
        "status": "ok",
    }
