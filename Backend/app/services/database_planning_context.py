from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from app.services.database_requirement_schema import derive_required_database_schema
from app.services.database_schema_diff import (
    compile_database_task_intents,
    diff_database_schema,
)
from app.services.database_schema_summary import (
    dict_items,
    inspect_mysql_schema,
    target_summary,
)

_DATABASE_ORIGIN_KINDS = {
    "mysql",
    "mysql_existing",
    "mysql_new_table",
    "database",
    "db",
}
_EXTERNAL_ORIGIN_KINDS = {
    "third_party",
    "external_api",
    "http_api",
    "api",
    "rest_api",
}
def prepare_database_planning_context(
    project_plan: dict[str, Any],
    build_context: dict[str, Any],
) -> dict[str, Any]:
    """为任务规划阶段准备新版数据库上下文、差异和后续任务意图。"""

    targets = database_origin_targets(project_plan, build_context)
    if not targets:
        return _planning_skipped(
            "no_database_origin_endpoint",
            "当前构建范围没有来源于数据库的接口。",
        )

    probe_target = _probe_target(targets)
    summary = inspect_mysql_schema(probe_target)
    if summary.get("status") == "connection_failed":
        required_schema = derive_required_database_schema(targets)
        return {
            "schema_version": "database-context.v1",
            "status": "connection_failed",
            "connection": _connection_summary(summary, connected=False),
            "actual_schema": {},
            "required_schema": required_schema,
            "gaps": [],
            "resolution_items": required_schema.get("resolution_items") or [],
            "task_intents": [],
            "captured_at": datetime.now(UTC).isoformat(),
            "summary": summary.get("message") or "数据库连接失败。",
            "targets": [target_summary(target) for target in targets],
        }

    actual_schema = _actual_schema_from_summary(summary)
    required_schema = derive_required_database_schema(targets)
    gaps = diff_database_schema(
        actual_schema=actual_schema,
        required_schema=required_schema,
    )
    task_intents = compile_database_task_intents(gaps)
    return {
        "schema_version": "database-context.v1",
        "status": "completed",
        "connection": _connection_summary(summary, connected=True),
        "actual_schema": actual_schema,
        "required_schema": required_schema,
        "gaps": gaps,
        "resolution_items": required_schema.get("resolution_items") or [],
        "task_intents": task_intents,
        "captured_at": datetime.now(UTC).isoformat(),
        "summary": _planning_human_summary(actual_schema, gaps),
        "targets": [target_summary(target) for target in targets],
    }


def database_context_requirement(
    project_plan: dict[str, Any],
    build_context: dict[str, Any],
) -> dict[str, Any]:
    """根据已确认 EndpointDetail.data_origin 判断数据库上下文节点是否应执行。"""

    targets = database_origin_targets(project_plan, build_context)
    if targets:
        return {
            "required": True,
            "status": "required",
            "reason": "database_data_origin",
            "targets": [target_summary(target) for target in targets],
        }
    return {
        "required": False,
        "status": "not_required",
        "reason": "no_database_data_origin",
        "targets": [],
    }


def database_origin_targets(
    project_plan: dict[str, Any],
    build_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """从当前构建上下文中找出 EndpointDetail 明确声明为数据库来源的接口。"""

    return [
        target
        for target in _endpoint_targets(project_plan, build_context)
        if _data_origin_kind(target.get("endpoint_detail")) in _DATABASE_ORIGIN_KINDS
    ]


def endpoint_detail_uses_database(endpoint_detail: Any) -> bool:
    """判断单个 EndpointDetail 是否明确声明数据来源为数据库。"""

    return endpoint_detail_origin_kind(endpoint_detail) in _DATABASE_ORIGIN_KINDS


def endpoint_detail_origin_kind(endpoint_detail: Any) -> str:
    """读取 EndpointDetail.data_origin 的显式来源类型，供路由和 Unit 过滤复用。"""

    if not isinstance(endpoint_detail, dict):
        return ""
    data_origin = endpoint_detail.get("data_origin")
    if not isinstance(data_origin, dict):
        return ""
    effective_source = data_origin.get("effective_source")
    effective_kind = (
        str(effective_source.get("kind") or "")
        if isinstance(effective_source, dict)
        else ""
    )
    source_type = str(data_origin.get("source_type") or "")
    kind = (effective_kind or source_type).strip().lower()
    return kind if kind not in _EXTERNAL_ORIGIN_KINDS else "external_api"


def _endpoint_targets(
    project_plan: dict[str, Any],
    build_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """把当前构建范围中的 endpoint 归一化为数据库扫描候选目标。"""

    endpoint_details = {
        str(detail.get("endpoint_id") or ""): detail
        for detail in dict_items(build_context.get("direct_endpoint_details"))
        if detail.get("endpoint_id")
    }
    endpoint_ids = {
        str(item)
        for item in build_context.get("endpoint_ids") or []
        if str(item).strip()
    }
    api_contract_ids = {
        str(item)
        for item in build_context.get("api_contract_ids") or []
        if str(item).strip()
    }
    targets: list[dict[str, Any]] = []
    for contract in dict_items(project_plan.get("api_contracts")):
        contract_id = str(contract.get("id") or "")
        if api_contract_ids and contract_id not in api_contract_ids:
            continue
        data_source = _data_source_for_contract(project_plan, contract)
        for endpoint in dict_items(contract.get("endpoints")):
            endpoint_id = str(endpoint.get("id") or "")
            if endpoint_ids and endpoint_id not in endpoint_ids:
                continue
            detail = endpoint_details.get(endpoint_id, {})
            target = {
                "api_contract_id": contract_id,
                "endpoint_id": endpoint_id,
                "method": str(
                    endpoint.get("method") or detail.get("method") or "GET"
                ).upper(),
                "path": str(endpoint.get("path") or detail.get("path") or ""),
                "summary": str(endpoint.get("summary") or detail.get("summary") or ""),
                "data_source_id": str(contract.get("data_source_id") or ""),
                "data_source": _target_data_source(data_source, detail),
                "api_contract": _contract_summary(contract, endpoint_ids),
                "endpoint_detail": detail,
            }
            targets.append(target)
    return targets


def _data_origin_kind(endpoint_detail: Any) -> str:
    """读取 EndpointDetail.data_origin 的显式来源类型，拒绝用 ProjectPlan 臆测。"""

    return endpoint_detail_origin_kind(endpoint_detail)


def _target_data_source(
    project_plan_source: dict[str, Any],
    endpoint_detail: dict[str, Any],
) -> dict[str, Any]:
    """把详情中的真实来源提示合并为工具扫描目标，ProjectPlan 只作标识补充。"""

    data_origin = endpoint_detail.get("data_origin")
    data_origin = data_origin if isinstance(data_origin, dict) else {}
    effective_source = data_origin.get("effective_source")
    effective_source = effective_source if isinstance(effective_source, dict) else {}
    return {
        **project_plan_source,
        **effective_source,
        "type": effective_source.get("kind")
        or data_origin.get("source_type")
        or project_plan_source.get("type"),
    }


def _data_source_for_contract(
    project_plan: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """按 API Contract 的 data_source_id 读取 ProjectPlan 数据源声明。"""

    data_source_id = str(contract.get("data_source_id") or "")
    return next(
        (
            item
            for item in dict_items(project_plan.get("data_sources"))
            if str(item.get("id") or "") == data_source_id
        ),
        {},
    )


def _contract_summary(contract: dict[str, Any], endpoint_ids: set[str]) -> dict[str, Any]:
    """保留当前接口相关 API Contract，供任务规划模型对照字段契约。"""

    return {
        **contract,
        "endpoints": [
            endpoint
            for endpoint in dict_items(contract.get("endpoints"))
            if not endpoint_ids or str(endpoint.get("id") or "") in endpoint_ids
        ],
    }


def _probe_target(targets: list[dict[str, Any]]) -> dict[str, Any]:
    """为一次数据库探测选择代表性目标，同时保留全部来源标识。"""

    first = dict(targets[0]) if targets else {}
    first["all_targets"] = [target_summary(target) for target in targets]
    return first


def _connection_summary(summary: dict[str, Any], *, connected: bool) -> dict[str, Any]:
    """生成脱敏连接摘要，失败时只暴露错误类别和值为空的配置键。"""

    return {
        "status": "connected" if connected else "failed",
        "source": "get_mysql_table_info",
        "database": summary.get("database"),
        "reason": summary.get("reason"),
        "message": summary.get("message"),
    }


def _actual_schema_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """把工具摘要转换成新版 actual_schema，并生成结构哈希。"""

    tables = list(summary.get("tables") or [])
    payload = {
        "database": summary.get("database"),
        "database_exists": summary.get("database_exists") is not False,
        "tables": tables,
    }
    return {
        **payload,
        "schema_hash": _stable_hash(payload),
    }


def _planning_skipped(reason: str, message: str) -> dict[str, Any]:
    """构造新版数据库上下文跳过结果。"""

    return {
        "schema_version": "database-context.v1",
        "status": "skipped",
        "reason": reason,
        "connection": {},
        "actual_schema": {},
        "required_schema": {},
        "gaps": [],
        "resolution_items": [],
        "task_intents": [],
        "targets": [],
        "captured_at": datetime.now(UTC).isoformat(),
        "summary": message,
    }


def _planning_human_summary(
    actual_schema: dict[str, Any],
    gaps: list[dict[str, Any]],
) -> str:
    """生成任务规划模型可读的新版数据库摘要总览。"""

    database = actual_schema.get("database") or ""
    table_count = len(actual_schema.get("tables") or [])
    if gaps:
        return f"已连接数据库 {database}，发现 {len(gaps)} 个结构差异，将转为数据库任务。"
    return f"已连接数据库 {database}，当前 {table_count} 张表满足本轮接口需求。"


def _stable_hash(value: Any) -> str:
    """为新版数据库上下文中的结构片段生成稳定哈希。"""

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return sha256(encoded.encode("utf-8")).hexdigest()
