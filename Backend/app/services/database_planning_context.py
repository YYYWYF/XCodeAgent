from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from app.domain.models import DatabasePlanningContext
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
_UNKNOWN_ORIGIN_KINDS = {
    "",
    "unknown",
    "needs_user_confirmation",
    "missing",
}


def prepare_database_planning_context(
    project_plan: dict[str, Any],
    build_context: dict[str, Any],
) -> dict[str, Any]:
    """为任务规划阶段准备真实数据库摘要，供数据库 Unit 与后端 Unit 拆分使用。"""

    targets = database_origin_targets(project_plan, build_context)
    if not targets:
        return _planning_skipped(
            "no_database_origin_endpoint",
            "当前构建范围没有来源于数据库的接口。",
        )

    contexts: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    for target in targets:
        data_source_id = str(target.get("data_source_id") or "")
        if data_source_id in seen_sources:
            continue
        seen_sources.add(data_source_id)
        summary = inspect_mysql_schema(target)
        if summary.get("status") != "completed":
            failures.append(summary)
            continue
        contexts.append(
            _planning_context_from_summary(
                data_source_id=data_source_id,
                summary=summary,
                target=target,
            )
        )

    status = "completed" if contexts else "failed"
    return {
        "status": status,
        "source": "get_mysql_table_info",
        "contexts": contexts,
        "targets": [target_summary(target) for target in targets],
        "failures": failures,
        "summary": _planning_human_summary(contexts, failures),
        "todo": "当前数据库连接信息从 .env 的 MYSQL_* 变量读取；后续改为 AG-UI 页面输入或选择。",
    }


def database_context_requirement(
    project_plan: dict[str, Any],
    build_context: dict[str, Any],
) -> dict[str, Any]:
    """根据已确认 EndpointDetail.data_origin 判断数据库上下文节点是否应执行。"""

    targets = database_origin_targets(project_plan, build_context)
    unresolved = _unresolved_origin_targets(project_plan, build_context)
    if targets:
        return {
            "required": True,
            "status": "required",
            "reason": "database_data_origin",
            "targets": [target_summary(target) for target in targets],
        }
    if unresolved:
        return {
            "required": False,
            "status": "blocked",
            "reason": "unresolved_data_origin",
            "message": "接口详细设计中的 data_origin 未明确数据来源，不能继续任务规划。",
            "targets": [target_summary(target) for target in unresolved],
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


def _unresolved_origin_targets(
    project_plan: dict[str, Any],
    build_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """找出已确认详情中数据来源缺失或待用户确认的接口。"""

    return [
        target
        for target in _endpoint_targets(project_plan, build_context)
        if _data_origin_kind(target.get("endpoint_detail")) in _UNKNOWN_ORIGIN_KINDS
    ]


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


def _planning_context_from_summary(
    *,
    data_source_id: str,
    summary: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    """把工具摘要转换为稳定的 DatabasePlanningContext 字典。"""

    tables = list(summary.get("tables") or [])
    digest_payload = {
        "data_source_id": data_source_id,
        "summary": summary.get("summary"),
        "tables": tables,
    }
    schema_hash = sha256(
        json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    context = DatabasePlanningContext(
        data_source_id=data_source_id,
        summary=str(summary.get("summary") or ""),
        tables=tables,
        captured_at=datetime.now(UTC).isoformat(),
        source="get_mysql_table_info",
        schema_hash=schema_hash,
    )
    return {
        **asdict(context),
        "database": summary.get("database"),
        "scope": summary.get("scope", {}),
        "target": target_summary(target),
        "api_contract": target.get("api_contract") or {},
        "endpoint_detail": target.get("endpoint_detail") or {},
    }


def _planning_skipped(reason: str, message: str) -> dict[str, Any]:
    """构造任务规划数据库上下文跳过结果。"""

    return {
        "status": "skipped",
        "source": "get_mysql_table_info",
        "reason": reason,
        "message": message,
        "contexts": [],
        "targets": [],
        "failures": [],
        "summary": message,
    }


def _planning_human_summary(
    contexts: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> str:
    """生成任务规划模型可读的数据库摘要总览。"""

    if contexts:
        source_ids = ", ".join(str(item.get("data_source_id") or "") for item in contexts)
        return f"已获取真实数据库摘要，覆盖数据源：{source_ids}。"
    if failures:
        return str(failures[0].get("message") or failures[0].get("summary") or "数据库摘要获取失败。")
    return "未获取数据库摘要。"
