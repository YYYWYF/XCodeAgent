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
    text_items,
)
from app.services.entity_definitions import (
    confirmed_entity_designs,
    data_source_type_label,
    entity_design_source_type,
    plan_data_sources,
)


def contract_source_type(project_plan: dict[str, Any], contract: Any) -> str:
    """按契约绑定实体的已确认设计返回首个数据源类型；实体未完成设计时返回空。"""

    source_types = _contract_entity_source_types(project_plan, contract)
    return source_types[0] if source_types else ""


def contract_uses_database(project_plan: dict[str, Any], contract: Any) -> bool:
    """判断契约绑定实体中是否存在数据源为数据库的实体。"""

    return "database" in _contract_entity_source_types(project_plan, contract)


def _contract_entity_source_types(
    project_plan: dict[str, Any],
    contract: Any,
) -> list[str]:
    """按契约 entity_ids 顺序返回绑定实体已确认设计的有序去重数据源类型。"""

    result: list[str] = []
    for detail in confirmed_entity_designs(project_plan, contract):
        source_type = entity_design_source_type(detail)
        if source_type and source_type not in result:
            result.append(source_type)
    return result


def prepare_database_planning_context(
    project_plan: dict[str, Any],
    build_context: dict[str, Any],
    workspace_root: str | None = None,
) -> dict[str, Any]:
    """为任务规划阶段准备新版数据库上下文、差异和后续任务意图。"""

    targets = database_origin_targets(project_plan, build_context)
    if not targets:
        return _planning_skipped(
            "no_database_origin_endpoint",
            "当前构建范围没有来源于数据库的接口。",
        )

    probe_target = _probe_target(targets)
    summary = inspect_mysql_schema(probe_target, workspace_root)
    data_sources = plan_data_sources(project_plan)
    new_table_entity_ids = _new_table_entity_ids(project_plan, targets)
    if summary.get("status") == "connection_failed":
        required_schema = derive_required_database_schema(
            targets,
            data_sources=data_sources,
            new_table_entity_ids=new_table_entity_ids,
        )
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
    required_schema = derive_required_database_schema(
        targets,
        data_sources=data_sources,
        new_table_entity_ids=new_table_entity_ids,
    )
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


def _new_table_entity_ids(
    project_plan: dict[str, Any],
    targets: list[dict[str, Any]],
) -> set[str]:
    """收集实体设计已确认建表的实体，仅这些实体参与建表编译。"""

    del project_plan
    result: set[str] = set()
    for target in targets:
        for entity_design in dict_items(target.get("entity_designs")):
            entity_id = str(entity_design.get("entity_id") or "").strip()
            if entity_id and _entity_design_requires_new_table(entity_design):
                result.add(entity_id)
    return result


def _entity_design_requires_new_table(entity_design: dict[str, Any]) -> bool:
    """按实体设计已确认的建表操作判断该实体是否需要编译目标表。"""

    database_design = (
        entity_design.get("database_design")
        if isinstance(entity_design.get("database_design"), dict)
        else {}
    )
    table_generation = (
        database_design.get("table_generation")
        if isinstance(database_design.get("table_generation"), dict)
        else {}
    )
    if table_generation.get("approved"):
        return True
    return any(
        isinstance(operation, dict)
        and str(operation.get("operation") or "") == "create_table"
        for operation in database_design.get("database_operations") or []
    )


def database_context_requirement(
    project_plan: dict[str, Any],
    build_context: dict[str, Any],
) -> dict[str, Any]:
    """根据接口绑定实体的已确认数据源判断数据库上下文节点是否应执行。"""

    targets = database_origin_targets(project_plan, build_context)
    if targets:
        return {
            "required": True,
            "status": "required",
            "reason": "database_entity_source",
            "targets": [target_summary(target) for target in targets],
        }
    return {
        "required": False,
        "status": "not_required",
        "reason": "no_database_entity_source",
        "targets": [],
    }


def database_origin_targets(
    project_plan: dict[str, Any],
    build_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """从当前构建上下文中找出绑定实体中存在数据库数据源的接口。"""

    return [
        target
        for target in _endpoint_targets(project_plan, build_context)
        if any(
            entity_design_source_type(design) == "database"
            for design in dict_items(target.get("entity_designs"))
        )
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
    entity_ids = {
        str(item)
        for item in build_context.get("entity_ids") or []
        if str(item).strip()
    }
    targets: list[dict[str, Any]] = []
    for contract in dict_items(project_plan.get("api_contracts")):
        contract_id = str(contract.get("id") or "")
        contract_entity_ids = set(text_items(contract.get("entity_ids")))
        if entity_ids and not (contract_entity_ids & entity_ids):
            continue
        contract_entity_designs = confirmed_entity_designs(project_plan, contract)
        contract_source_types = _contract_entity_source_types(project_plan, contract)
        data_source = _data_source_for_contract(
            contract_source_types,
            contract_entity_designs,
        )
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
                "data_source_id": _contract_data_source_id(contract_source_types),
                "data_source": data_source,
                "entity_ids": text_items(contract.get("entity_ids")),
                "entity_designs": contract_entity_designs,
                "api_contract": _contract_summary(contract, endpoint_ids),
                "endpoint_detail": detail,
            }
            targets.append(target)
    return targets


def _contract_data_source_id(source_types: list[str]) -> str:
    """数据库优先选取契约数据源标识，保持混合契约下数据库目标编译基线稳定。"""

    if "database" in source_types:
        return "database"
    return source_types[0] if source_types else ""


def _data_source_for_contract(
    source_types: list[str],
    entity_designs: list[dict[str, Any]],
) -> dict[str, Any]:
    """按契约实体设计的源类型集合构造虚拟数据源摘要。

    数据库实体优先作为目标标识，保证混合契约时数据库目标编译基线稳定；
    接口本身不再持有数据源身份，实体设计才是唯一事实来源。
    """

    if not source_types:
        return {}
    data_source_id = _contract_data_source_id(source_types)
    return {
        "id": data_source_id,
        "name": data_source_type_label(data_source_id),
        "type": data_source_id,
        "entities": [
            design
            for design in entity_designs
            if entity_design_source_type(design) == data_source_id
        ],
    }


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
