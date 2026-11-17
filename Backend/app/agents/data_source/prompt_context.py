from __future__ import annotations

import re
from typing import Any

from app.agents.data_source.execution_config import (
    is_bootstrap_task,
    task_data_source_types,
    task_endpoint_designs,
    task_execution_config,
    task_required_instruction_paths,
)
from app.agents.data_source.business_projection import business_api_design


_SOURCE_REFERENCE_DIRECTORIES = {"database", "external_api"}
_OUTER_VERIFICATION_POLICY = "outer_integration_test_only"


def execution_task_packet(
    project_plan: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any]:
    """把 Build 任务和已确认设计编译为 Java Agent 可直接执行的最小任务包。"""

    config = task_execution_config(task)
    return {
        **({"id": task["id"]} if "id" in task else {}),
        **({"unit_id": task["unit_id"]} if "unit_id" in task else {}),
        "kind": config.kind,
        "stage": config.stage,
        "execution_steps": _task_execution_steps(task),
        "allowed_paths": list(task.get("allowed_paths") or []),
        **({"approved_add_path": task["approved_add_path"]} if task.get("approved_add_path") else {}),
        "change_scope": [
            dict(item)
            for item in task.get("change_scope") or []
            if isinstance(item, dict)
        ],
        "instruction_paths": task_required_instruction_paths(task),
        "implementation_contract": task_implementation_contract(project_plan, task),
    }


def task_implementation_contract(
    project_plan: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any]:
    """按基础环境、骨架、单接口业务和共享 Controller 编译各自的只读契约。"""

    if is_bootstrap_task(task):
        source_types = task_data_source_types([task])
        return {
            "kind": "bootstrap",
            "java_version": "8",
            "build_system": "maven",
            "framework": "spring_boot",
            "capabilities": [
                capability
                for source_type, capability in (
                    ("database", "mybatis_plus_mysql"),
                    ("external_api", "spring_cloud_openfeign"),
                )
                if source_type in source_types
            ],
            **(
                {"persistence": "mybatis_plus", "database": "mysql"}
                if "database" in source_types
                else {}
            ),
            **(
                {
                    "http_client": "openfeign",
                    "template_dependencies": {
                        "spring_boot_version": "2.7.2",
                        "spring_cloud_version": "2021.0.3",
                        "starter": (
                            "org.springframework.cloud:"
                            "spring-cloud-starter-openfeign"
                        ),
                        "bom": (
                            "org.springframework.cloud:"
                            "spring-cloud-dependencies"
                        ),
                        "activation": "EnableFeignClients",
                    },
                }
                if "external_api" in source_types
                else {}
            ),
            "configuration_policy": "reuse_existing_then_fill_missing",
            "verification_policy": _OUTER_VERIFICATION_POLICY,
        }

    stage = task_execution_config(task).stage
    contract_ids, endpoint_ids, entity_ids = _task_scope_ids([task])
    api_contracts = _scoped_api_contracts(
        project_plan,
        contract_ids,
        endpoint_ids,
        entity_ids,
    )
    endpoint_designs = task_endpoint_designs(task)
    if len(api_contracts) != 1 or {item["id"] for item in api_contracts[0]["endpoints"]} != endpoint_ids:
        raise ValueError("后端模块任务的接口范围必须来自同一个已确认 API Contract。")
    design_ids = {
        item.get("endpointId") for item in endpoint_designs
        if item.get("apiContractId") == api_contracts[0]["id"]
    }
    if not endpoint_ids or design_ids != endpoint_ids or len(endpoint_designs) != len(design_ids):
        raise ValueError("后端模块任务必须为每个范围内的 Endpoint 携带一个已确认 API 设计。")
    contract = {
        "kind": "endpoint",
        "language": {"java_version": "8"},
        "verification_policy": _OUTER_VERIFICATION_POLICY,
    }
    if stage == "foundation":
        return {**contract, "source_snapshots": _foundation_source_snapshots(endpoint_designs)}
    contract["api_contract"] = api_contracts[0]
    if stage == "controller":
        return {**contract, "authorization_constraints": _endpoint_authorization_constraints(task)}
    if len(endpoint_designs) != 1:
        raise ValueError("后端业务任务必须且只能携带一个已确认 Endpoint API 设计。")
    return {
        "api_contract": api_contracts[0],
        "api_design": business_api_design(endpoint_designs[0]),
    }


def _foundation_source_snapshots(endpoint_designs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为基础阶段保留表结构和上游连接，去掉外部 Operation 的业务合同。"""

    snapshots: list[dict[str, Any]] = []
    for design in endpoint_designs:
        for snapshot in _dict_items(design.get("sourceSnapshots")):
            source_type = snapshot.get("sourceType")
            if source_type == "external_api":
                snapshot = {
                    **snapshot,
                    "details": {"connection": snapshot.get("details", {}).get("connection")},
                }
            if source_type in _SOURCE_REFERENCE_DIRECTORIES and snapshot not in snapshots:
                snapshots.append(snapshot)
    return snapshots


def _endpoint_authorization_constraints(task: dict[str, Any]) -> list[dict[str, Any]]:
    """将共享 Controller 的权限切片按 Endpoint 拆分为独立的只读契约。"""

    authorization = task["source_refs"].get("authorization")
    if not isinstance(authorization, dict):
        return []
    constant_by_key = {
        str(item["resourceKey"]): str(item["name"])
        for item in _dict_items(authorization.get("authConstants"))
        if item.get("resourceKey") and item.get("name")
    }
    return [
        _endpoint_authorization_contract(endpoint, constant_by_key)
        for endpoint in _dict_items(authorization.get("endpoints"))
    ]


def _endpoint_authorization_contract(
    endpoint: dict[str, Any], constant_by_key: dict[str, str],
) -> dict[str, Any]:
    """核对路由和资源常量，只为该 Endpoint 下发相关权限。"""

    contract_id = str(endpoint.get("apiContractId") or "").strip()
    endpoint_id = str(endpoint.get("endpointId") or "").strip()
    http_method = str(endpoint.get("httpMethod") or "").strip().upper()
    path = str(endpoint.get("path") or "").strip()
    resource_keys = _string_items(endpoint.get("operationResourceKeys"))
    if not contract_id or not endpoint_id or not http_method or not path.startswith("/"):
        raise ValueError("Backend Endpoint Task 的平台权限切片缺少唯一 Endpoint HTTP 身份。")
    if str(endpoint.get("semantics") or "") != "ANY_OF":
        raise ValueError("Backend Endpoint Task 的权限语义必须是 ANY_OF。")
    if any(key not in constant_by_key for key in resource_keys):
        raise ValueError("Backend Endpoint Task 的 AuthConstants 符号与操作资源集合不一致。")
    return {
        "endpointIdentity": {
            "apiContractId": contract_id,
            "endpointId": endpoint_id,
            "httpMethod": http_method,
            "path": path,
        },
        "operationResourceKeys": resource_keys,
        "semantics": "ANY_OF",
        "authConstants": [
            {"name": constant_by_key[key], "resourceKey": key}
            for key in resource_keys
        ],
    }


def _task_execution_steps(task: dict[str, Any]) -> list[str]:
    """从任务中文编号描述提取有界执行步骤，不把整段自由文本注入执行契约。"""

    description = str(task.get("description") or "")[:12_000]
    steps: list[str] = []
    for line in description.splitlines():
        match = re.match(r"^\s*\d+[.、)]\s*(.+?)\s*$", line)
        if not match:
            continue
        step = match.group(1).strip()[:1000]
        if step:
            steps.append(step)
        if len(steps) >= 50:
            break
    return steps


def _task_scope_ids(tasks: list[dict[str, Any]]) -> tuple[set[str], set[str], set[str]]:
    """汇总当前批次真实涉及的契约、接口和 TechnicalPlan Entity 标识。"""

    contract_ids: set[str] = set()
    endpoint_ids: set[str] = set()
    entity_ids: set[str] = set()
    for task in tasks:
        source_refs = task.get("source_refs")
        source_refs = source_refs if isinstance(source_refs, dict) else {}
        target = source_refs.get("target")
        target = target if isinstance(target, dict) else {}
        contract_id = str(target.get("api_contract_id") or "").strip()
        if contract_id:
            contract_ids.add(contract_id)
        endpoint_ids.update(_string_items(source_refs.get("endpoint_ids")))
        entity_ids.update(_string_items(source_refs.get("entity_ids")))
    return contract_ids, endpoint_ids, entity_ids


def _referenced_schema_names(value: Any) -> set[str]:
    """递归提取契约对象中的本地 Schema 引用名称。"""

    result: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$ref", "schema_ref", "request_schema_ref", "response_schema_ref"}:
                reference = str(item or "").strip()
                if reference:
                    result.add(reference.rsplit("/", 1)[-1])
            result.update(_referenced_schema_names(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_referenced_schema_names(item))
    return result


def _scoped_contract_schemas(schemas: Any, endpoints: list[dict[str, Any]]) -> dict[str, Any]:
    """保留目标 Endpoint 直接或传递引用的命名 Schema。"""

    schema_map = schemas if isinstance(schemas, dict) else {}
    selected: dict[str, Any] = {}
    pending = list(_referenced_schema_names(endpoints))
    while pending:
        name = pending.pop()
        if name in selected or name not in schema_map:
            continue
        selected[name] = schema_map[name]
        pending.extend(_referenced_schema_names(schema_map[name]) - set(selected))
    return selected


def _scoped_api_contracts(
    project_plan: dict[str, Any],
    contract_ids: set[str],
    endpoint_ids: set[str],
    entity_ids: set[str],
) -> list[dict[str, Any]]:
    """从 ProjectPlan 中只投射当前任务涉及的 API 契约片段。"""

    result: list[dict[str, Any]] = []
    for contract in _dict_items(project_plan.get("api_contracts")):
        contract_id = str(contract.get("id") or "")
        if contract_id not in contract_ids:
            continue
        endpoints = [
            dict(endpoint)
            for endpoint in _dict_items(contract.get("endpoints"))
            if str(endpoint.get("id") or "") in endpoint_ids
        ]
        if not endpoints:
            continue
        result.append(
            {
                "id": contract_id,
                "entity_ids": sorted(
                    str(entity_id).strip()
                    for entity_id in contract.get("entity_ids") or []
                    if str(entity_id).strip() and str(entity_id).strip() in entity_ids
                ),
                "base_path": contract.get("base_path"),
                "authentication": contract.get("authentication"),
                "schemas": _scoped_contract_schemas(contract.get("schemas"), endpoints),
                "endpoints": endpoints,
            }
        )
    return result


def _string_items(value: Any) -> list[str]:
    """把不可信列表规整为去空字符串列表。"""

    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """把不可信列表规整为字典列表。"""

    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )
