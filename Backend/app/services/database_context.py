from __future__ import annotations

import os
from typing import Any

from app.services.database_schema_summary import (
    dict_items,
    inspect_mysql_schema,
    is_database_data_source,
    target_summary,
)

ENDPOINT_DATABASE_CONTEXT_ENV = "XCODEAGENT_ENDPOINT_DATABASE_CONTEXT_ENABLED"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def prepare_endpoint_database_context(
    project_plan: dict[str, Any],
    endpoint_context: dict[str, Any],
    workspace_root: str | None = None,
) -> dict[str, Any]:
    """为单个接口详细设计准备精简数据库上下文。"""

    if not _database_context_enabled():
        return _skipped("disabled", "数据库上下文扫描开关未启用。")
    if not isinstance(project_plan, dict):
        return _skipped("missing_project_plan", "缺少 ProjectPlan，无法判断数据源。")

    target = _endpoint_target(project_plan, endpoint_context)
    if not target.get("api_contract_id") or not target.get("endpoint_id"):
        return _skipped("missing_endpoint_selection", "缺少接口选择信息。")
    if not is_database_data_source(target.get("data_source")):
        return _skipped(
            "not_database_source", "当前接口的数据源不是数据库。", target=target
        )

    return inspect_mysql_schema(target, workspace_root)


def _database_context_enabled() -> bool:
    """读取接口数据库上下文扫描开关。"""

    return os.getenv(ENDPOINT_DATABASE_CONTEXT_ENV, "").strip().lower() in _TRUE_VALUES


def _endpoint_target(
    project_plan: dict[str, Any],
    endpoint_context: dict[str, Any],
) -> dict[str, Any]:
    """从已定位的 endpoint_context 解析数据源目标。"""

    api_contract_id = str(endpoint_context.get("api_contract_id") or "")
    endpoint_id = str(endpoint_context.get("endpoint_id") or "")
    data_source_id = str(endpoint_context.get("data_source_id") or api_contract_id)
    data_source = next(
        (
            item
            for item in dict_items(project_plan.get("data_sources"))
            if str(item.get("id") or "") == data_source_id
        ),
        {},
    )
    return {
        "api_contract_id": api_contract_id,
        "endpoint_id": endpoint_id,
        "method": str(endpoint_context.get("method") or "GET").upper(),
        "path": str(endpoint_context.get("path") or ""),
        "summary": str(endpoint_context.get("summary") or ""),
        "data_source_id": data_source_id,
        "data_source": data_source,
    }


def _skipped(
    reason: str,
    message: str,
    *,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造跳过扫描的统一状态。"""

    return {
        "status": "skipped",
        "enabled": _database_context_enabled(),
        "reason": reason,
        "message": message,
        **({"target": target_summary(target)} if target else {}),
    }
