from __future__ import annotations

from typing import Any

from app.services.builtin_skills import (
    BUILTIN_SKILLS_VIRTUAL_ROOT,
    SPRINGBOOT_EXTERNAL_API_GENERATE_SKILL_NAME,
    SPRINGBOOT_MYBATIS_GENERATE_SKILL_NAME,
)


_SOURCE_SKILL_NAMES = {
    "database": SPRINGBOOT_MYBATIS_GENERATE_SKILL_NAME,
    "external_api": SPRINGBOOT_EXTERNAL_API_GENERATE_SKILL_NAME,
}
_SOURCE_SKILL_ORDER = ("database", "external_api")


def task_entity_designs(task: dict[str, Any]) -> list[dict[str, Any]]:
    """读取单个任务已由 Unit 编译器裁剪过的实体设计。"""

    source_refs = task.get("source_refs")
    source_refs = source_refs if isinstance(source_refs, dict) else {}
    designs = source_refs.get("entity_designs")
    return (
        [dict(item) for item in designs if isinstance(item, dict)]
        if isinstance(designs, list)
        else []
    )


def task_data_source_types(tasks: list[dict[str, Any]]) -> set[str]:
    """从当前派发任务的实体设计提取有界数据源类型集合。"""

    return {
        str(design.get("data_source_type") or "").strip()
        for task in tasks
        for design in task_entity_designs(task)
        if str(design.get("data_source_type") or "").strip()
    }


def task_required_skill_paths(task: dict[str, Any]) -> list[str]:
    """按单个任务的实体数据源类型映射必须读取的内置 Skill。"""

    source_types = {
        str(design.get("data_source_type") or "").strip()
        for design in task_entity_designs(task)
        if str(design.get("data_source_type") or "").strip()
    }
    unsupported = source_types - set(_SOURCE_SKILL_NAMES)
    if "static" in unsupported:
        raise ValueError(
            f"DataSource 后端任务 {task.get('id') or '<unknown>'} 不得处理 static 实体。"
        )
    if unsupported:
        raise ValueError(
            f"DataSource 后端任务 {task.get('id') or '<unknown>'} 包含非法数据源类型："
            f"{', '.join(sorted(unsupported))}。"
        )
    return [
        f"{BUILTIN_SKILLS_VIRTUAL_ROOT}{_SOURCE_SKILL_NAMES[source_type]}/SKILL.md"
        for source_type in _SOURCE_SKILL_ORDER
        if source_type in source_types
    ]


def execution_task_packet(task: dict[str, Any]) -> dict[str, Any]:
    """把 Build 任务裁剪为 DataSource Agent 可执行的最小任务包。"""

    packet = {
        key: task[key]
        for key in (
            "id",
            "unit_id",
            "title",
            "description",
            "allowed_paths",
            "target_files",
            "change_scope",
        )
        if key in task
    }
    packet["source_refs"] = _compact_task_source_refs(task)
    packet["required_skill_paths"] = task_required_skill_paths(task)
    return packet


def data_source_execution_context(
    project_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造仅含当前 TechnicalPlan API 与完整实体数据源绑定的执行上下文。"""

    contract_ids, endpoint_ids, entity_ids = _task_scope_ids(tasks)
    entity_designs = [
        dict(detail)
        for detail in _dict_items(project_plan.get("entity_detail_plans"))
        if str(detail.get("entity_id") or "") in entity_ids
        and str(detail.get("status") or "") == "confirmed"
    ]
    return {
        "api_contracts": _scoped_api_contracts(
            project_plan,
            contract_ids,
            endpoint_ids,
            entity_ids,
        ),
        "entity_designs": entity_designs,
    }


def _compact_task_source_refs(task: dict[str, Any]) -> dict[str, Any]:
    """投射执行任务所需的稳定标识，避免重复注入实体设计正文。"""

    value = task.get("source_refs")
    source_refs = value if isinstance(value, dict) else {}
    return {
        key: source_refs[key]
        for key in (
            "type",
            "target",
            "technical_plan_endpoint",
            "technical_plan_endpoints",
            "endpoint_ids",
            "entity_ids",
        )
        if key in source_refs
    }


def _task_scope_ids(tasks: list[dict[str, Any]]) -> tuple[set[str], set[str], set[str]]:
    """汇总当前批次真实涉及的契约、接口和实体标识。"""

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
                "entity_ids": [
                    item
                    for item in _string_items(contract.get("entity_ids"))
                    if not entity_ids or item in entity_ids
                ],
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
