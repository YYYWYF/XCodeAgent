from __future__ import annotations

import re
from typing import Any

from app.services.builtin_skills import (
    BUILTIN_SKILLS_VIRTUAL_ROOT,
    SPRINGBOOT_BACKEND_GENERATE_SKILL_NAME,
    SPRINGBOOT_TEMPLATE_BOUNDARY_SKILL_NAME,
)
from app.services.api_design import api_design_business_descriptions
from app.services.template_scaffold_injection import prebuilt_files_for_plan


_SOURCE_REFERENCE_DIRECTORIES = {
    "database": "database",
    "external_api": "external-api",
}
_SOURCE_SKILL_ORDER = ("database", "external_api")
_BOOTSTRAP_UNIT_ID = "backend:bootstrap"
_OUTER_VERIFICATION_POLICY = "outer_integration_test_only"


def task_endpoint_designs(task: dict[str, Any]) -> list[dict[str, Any]]:
    """读取单个任务已由 Unit 编译器裁剪过的 Endpoint API 设计。"""

    source_refs = task.get("source_refs")
    source_refs = source_refs if isinstance(source_refs, dict) else {}
    designs = source_refs.get("endpoint_designs")
    return (
        [dict(item) for item in designs if isinstance(item, dict)]
        if isinstance(designs, list)
        else []
    )


def task_data_source_types(tasks: list[dict[str, Any]]) -> set[str]:
    """从当前派发任务的 API 设计来源快照提取数据源类型集合。"""

    return {
        str(snapshot.get("sourceType") or "").strip()
        for task in tasks
        for design in task_endpoint_designs(task)
        for snapshot in _dict_items(design.get("sourceSnapshots"))
        if str(snapshot.get("sourceType") or "").strip()
    }


def task_required_skill_paths(task: dict[str, Any]) -> list[str]:
    """按单个任务的 API 设计来源类型映射必须读取的内置 Skill。"""

    source_types = {
        str(snapshot.get("sourceType") or "").strip()
        for design in task_endpoint_designs(task)
        for snapshot in _dict_items(design.get("sourceSnapshots"))
        if str(snapshot.get("sourceType") or "").strip()
    }
    unsupported = source_types - set(_SOURCE_REFERENCE_DIRECTORIES)
    if unsupported:
        raise ValueError(
            f"DataSource 后端任务 {task.get('id') or '<unknown>'} 包含非法数据源类型："
            f"{', '.join(sorted(unsupported))}。"
        )
    if not source_types:
        return []
    return [
        f"{BUILTIN_SKILLS_VIRTUAL_ROOT}"
        f"{SPRINGBOOT_BACKEND_GENERATE_SKILL_NAME}/SKILL.md",
        f"{BUILTIN_SKILLS_VIRTUAL_ROOT}"
        f"{SPRINGBOOT_TEMPLATE_BOUNDARY_SKILL_NAME}/SKILL.md",
    ]


def task_required_instruction_paths(task: dict[str, Any]) -> list[str]:
    """展开任务必须读取的 Skill 入口与当前任务类型对应的条件参考文档。"""

    paths = task_required_skill_paths(task)
    if not paths:
        return paths
    source_types = task_data_source_types([task])
    reference_name = (
        "bootstrap.md"
        if _task_kind(task) == "bootstrap"
        else "layer-implementation.md"
    )
    skill_root = (
        f"{BUILTIN_SKILLS_VIRTUAL_ROOT}"
        f"{SPRINGBOOT_BACKEND_GENERATE_SKILL_NAME}/references/"
    )
    paths.extend(
        f"{skill_root}{_SOURCE_REFERENCE_DIRECTORIES[source_type]}/{reference_name}"
        for source_type in _SOURCE_SKILL_ORDER
        if source_type in source_types
    )
    # 后端模板修改边界 skill 的参考文档：让 Agent 知道预置代码和修改边界。
    boundary_root = (
        f"{BUILTIN_SKILLS_VIRTUAL_ROOT}"
        f"{SPRINGBOOT_TEMPLATE_BOUNDARY_SKILL_NAME}/references/"
    )
    paths.extend(
        [
            f"{boundary_root}module-layout.md",
            f"{boundary_root}entity-template.md",
            f"{boundary_root}repository-template.md",
            f"{boundary_root}dto-template.md",
            f"{boundary_root}controller-template.md",
        ]
    )
    return paths


def execution_task_packet(
    project_plan: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any]:
    """把 Build 任务和已确认设计编译为 Java Agent 可直接执行的最小任务包。"""

    kind = _task_kind(task)
    return {
        **({"id": task["id"]} if "id" in task else {}),
        **({"unit_id": task["unit_id"]} if "unit_id" in task else {}),
        "kind": kind,
        "stage": _task_stage(task),
        "execution_steps": _task_execution_steps(task),
        "allowed_paths": list(task.get("allowed_paths") or []),
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
    """按 bootstrap 或 endpoint 边界生成唯一且不混入全局计划的实现契约。"""

    if _task_kind(task) == "bootstrap":
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
            "prebuilt_files": prebuilt_files_for_plan(project_plan),
        }

    contract_ids, endpoint_ids, entity_ids = _task_scope_ids([task])
    api_contracts = _scoped_api_contracts(
        project_plan,
        contract_ids,
        endpoint_ids,
        entity_ids,
    )
    endpoint_designs = task_endpoint_designs(task)
    if len(endpoint_designs) != 1:
        raise ValueError("Backend Endpoint Task 必须且只能携带一个已确认 Endpoint API 设计。")
    source_refs = task.get("source_refs")
    source_refs = source_refs if isinstance(source_refs, dict) else {}
    return {
        "kind": "endpoint",
        "api_contract": api_contracts[0] if api_contracts else {},
        "api_design": endpoint_designs[0],
        "business_descriptions": [
            dict(item)
            for item in source_refs.get("business_descriptions") or api_design_business_descriptions(endpoint_designs)
            if isinstance(item, dict)
        ],
        "authorization_constraints": _endpoint_authorization_constraints(task),
        "language": {"java_version": "8"},
        "verification_policy": _OUTER_VERIFICATION_POLICY,
        "prebuilt_files": prebuilt_files_for_plan(project_plan),
    }


def _endpoint_authorization_constraints(task: dict[str, Any]) -> dict[str, Any] | None:
    """把平台注入的单 Endpoint 权限切片收敛为只读 Java 实现契约。"""

    source_refs = task.get("source_refs")
    source_refs = source_refs if isinstance(source_refs, dict) else {}
    authorization = source_refs.get("authorization")
    if not isinstance(authorization, dict):
        return None
    endpoints = [
        dict(item)
        for item in authorization.get("endpoints") or []
        if isinstance(item, dict)
    ]
    if len(endpoints) != 1:
        raise ValueError("Backend Endpoint Task 的平台权限切片必须恰好包含一个 Endpoint。")
    endpoint = endpoints[0]
    contract_id = str(endpoint.get("apiContractId") or "").strip()
    endpoint_id = str(endpoint.get("endpointId") or "").strip()
    http_method = str(endpoint.get("httpMethod") or "").strip().upper()
    path = str(endpoint.get("path") or "").strip()
    resource_keys = _string_items(endpoint.get("operationResourceKeys"))
    if not contract_id or not endpoint_id or not http_method or not path.startswith("/"):
        raise ValueError("Backend Endpoint Task 的平台权限切片缺少唯一 Endpoint HTTP 身份。")
    if str(endpoint.get("semantics") or "") != "ANY_OF":
        raise ValueError("Backend Endpoint Task 的权限语义必须是 ANY_OF。")
    constants = [
        {"name": str(item.get("name") or "").strip(), "resourceKey": str(item.get("resourceKey") or "").strip()}
        for item in authorization.get("authConstants") or []
        if isinstance(item, dict)
    ]
    constant_by_key = {item["resourceKey"]: item["name"] for item in constants if item["name"] and item["resourceKey"]}
    if resource_keys and set(constant_by_key) != set(resource_keys):
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


def _task_kind(task: dict[str, Any]) -> str:
    """根据稳定 Unit 标识区分基础设施任务与 Endpoint 实现任务。"""

    return "bootstrap" if str(task.get("unit_id") or "") == _BOOTSTRAP_UNIT_ID else "endpoint"


def _task_stage(task: dict[str, Any]) -> str:
    """从当前稳定任务 ID 提取执行阶段，避免 Agent 根据文件名猜测职责。"""

    if _task_kind(task) == "bootstrap":
        return "bootstrap"
    task_id = str(task.get("id") or "").strip()
    stage = task_id.rsplit("::", 1)[-1] if "::" in task_id else ""
    allowed = {
        "objects",
        "repository",
        "upstream",
        "service",
        "controller",
    }
    return stage if stage in allowed else "endpoint"


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
        endpoints = [
            dict(endpoint)
            for endpoint in _dict_items(contract.get("endpoints"))
            if str(endpoint.get("id") or "") in endpoint_ids
        ]
        if contract_id not in contract_ids and not endpoints:
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
