"""由冻结职责生成任务身份清单，供 Prompt 和缺失 ID 绑定共同使用。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.business_acceptance import canonical_frontend_api_module_path, frontend_api_task_id
from app.services.unit_generation_contracts import UnitGenerationContext


def _text(value: Any) -> str:
    """只接受无需修剪的非空正式身份。"""

    return value if isinstance(value, str) and value and value == value.strip() else ""


_ENDPOINT_STAGE_BY_KIND = {
    "backend.objects": "objects",
    "backend.repository": "repository",
    "backend.upstream": "upstream",
    "backend.application_service": "service",
    "backend.endpoint_controller": "controller",
}
_REQUIREMENT_PREFIX_BY_KIND = {
    "backend.objects": "backend.endpoint.objects",
    "backend.repository": "backend.repository",
    "backend.upstream": "backend.upstream",
    "backend.application_service": "backend.application_service",
    "backend.endpoint_controller": "backend.endpoint_controller",
}
_STAGE_ORDER = {
    "objects": 0,
    "repository": 1,
    "upstream": 1,
    "service": 2,
    "controller": 3,
}
PHYSICAL_SOURCE_TYPES = frozenset({"database", "external_api"})


def _source_types(refs: Mapping[str, Any]) -> tuple[str, ...] | None:
    """读取 Endpoint responsibility 携带的完整有序物理来源集合。"""

    values = refs.get("data_source_types")
    if not isinstance(values, (list, tuple)):
        return None
    result = tuple(value for value in values if _text(value))
    if (
        len(result) != len(values)
        or len(set(result)) != len(result)
        or tuple(sorted(result)) != result
        or set(result) - PHYSICAL_SOURCE_TYPES
    ):
        return None
    return result


def _endpoint_task_id(unit_id: str, stage: str) -> str:
    """使用已含 Endpoint 身份的 Unit ID 生成唯一阶段 Task ID。"""

    return f"{unit_id}::{stage}"


def backend_stage_manifest(
    context: UnitGenerationContext,
) -> tuple[dict[str, Any], ...]:
    """把 Endpoint responsibilities 编译为不含实体身份的固定阶段清单。"""

    result: list[dict[str, Any]] = []
    for requirement in context.generation_requirements:
        refs = requirement.source_refs
        kind = _text(refs.get("kind"))
        stage = _ENDPOINT_STAGE_BY_KIND.get(kind, "")
        api_contract_id = _text(refs.get("api_contract_id"))
        endpoint_id = _text(refs.get("endpoint_id"))
        target_id = _text(refs.get("target_id"))
        source_types = _source_types(refs)
        expected_unit_id = f"backend:endpoint:{api_contract_id}:{endpoint_id}"
        expected_requirement_id = (
            f"{_REQUIREMENT_PREFIX_BY_KIND.get(kind, '')}:{api_contract_id}:{endpoint_id}"
        )
        if (
            not stage
            or not api_contract_id
            or not endpoint_id
            or target_id != endpoint_id
            or source_types is None
            or expected_unit_id != context.unit_id
            or requirement.requirement_id != expected_requirement_id
            or refs.get("artifact") != "endpoint-api-design"
            or "entity_id" in refs
        ):
            raise ValueError(
                f"Backend Endpoint Unit {context.unit_id} 含无法绑定到当前 Endpoint 的职责。"
            )
        result.append({
            "requirement_id": requirement.requirement_id,
            "api_contract_id": api_contract_id,
            "endpoint_id": endpoint_id,
            "data_source_types": list(source_types),
            "stage": stage,
            "task_id": _endpoint_task_id(context.unit_id, stage),
            "deliverable_kind": kind,
        })
    stages = [item["stage"] for item in result]
    if len(stages) != len(set(stages)):
        raise ValueError(f"Backend Endpoint Unit {context.unit_id} 含重复 Endpoint responsibility。")
    retained_task_ids = {
        task_id
        for summary in context.dependency_context.get("retained_task_summaries", ())
        if isinstance(summary, Mapping)
        and (task_id := _text(summary.get("id")))
        and summary.get("unit_id") in {None, context.unit_id}
    }
    available_task_ids = {
        item["task_id"] for item in result
    } | retained_task_ids
    for item in result:
        source_types = set(item["data_source_types"])
        objects_id = _endpoint_task_id(context.unit_id, "objects")
        branch_ids = [
            _endpoint_task_id(context.unit_id, stage)
            for source_type, stage in (
                ("database", "repository"),
                ("external_api", "upstream"),
            )
            if source_type in source_types
            and _endpoint_task_id(context.unit_id, stage) in available_task_ids
        ]
        service_id = _endpoint_task_id(context.unit_id, "service")
        if item["stage"] == "objects":
            dependencies: list[str] = []
        elif item["stage"] in {"repository", "upstream"}:
            dependencies = [objects_id] if objects_id in available_task_ids else []
        elif item["stage"] == "service":
            dependencies = branch_ids or (
                [objects_id] if objects_id in available_task_ids else []
            )
        else:
            dependencies = (
                [service_id]
                if service_id in available_task_ids
                else branch_ids or ([objects_id] if objects_id in available_task_ids else [])
            )
        item["dependencies"] = dependencies
    return tuple(sorted(
        result,
        key=lambda item: (_STAGE_ORDER[item["stage"]], item["requirement_id"]),
    ))


def frontend_api_task_manifest(
    context: UnitGenerationContext,
) -> tuple[dict[str, Any], ...]:
    """把当前 frontend API requirements 编译为按 Contract 分组的稳定任务清单。"""

    grouped: dict[str, list[tuple[str, str]]] = {}
    for requirement in context.generation_requirements:
        refs = requirement.source_refs
        if _text(refs.get("kind")) != "frontend.api_module":
            raise ValueError(
                f"Frontend API Unit {context.unit_id} 含非 frontend.api_module 职责。"
            )
        contract_id = _text(refs.get("api_contract_id"))
        endpoint_id = _text(refs.get("endpoint_id"))
        if not contract_id or not endpoint_id:
            raise ValueError(
                f"Frontend API Unit {context.unit_id} 的职责缺少 Contract 或 Endpoint 身份。"
            )
        grouped.setdefault(contract_id, []).append((endpoint_id, requirement.requirement_id))

    manifest: list[dict[str, Any]] = []
    for contract_id in sorted(grouped):
        records = grouped[contract_id]
        endpoint_ids = tuple(sorted(endpoint_id for endpoint_id, _ in records))
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError(
                f"Frontend API Unit {context.unit_id} 含重复职责 {contract_id} 的 Endpoint。"
            )
        manifest.append({
            "api_contract_id": contract_id,
            "endpoint_ids": list(endpoint_ids),
            "requirement_ids": [
                requirement_id
                for _, requirement_id in sorted(records, key=lambda item: item[0])
            ],
            "task_id": frontend_api_task_id(context.unit_id, contract_id, endpoint_ids),
            "module_path": canonical_frontend_api_module_path(contract_id),
        })
    return tuple(manifest)


def unit_task_identity_manifest(context: UnitGenerationContext) -> dict[str, tuple[str, ...]]:
    """将当前全部增量职责分组到固定任务 ID，不读取模型内容或历史候选。"""

    if context.unit_kind == "agent":
        from app.services.runtime_agent_dag import agent_task_manifest
        return {item["task_id"]: (item["requirement_id"],) for item in agent_task_manifest(context)}
    if context.unit_kind == "backend" and context.unit_id.startswith("backend:endpoint:"):
        return {item["task_id"]: (item["requirement_id"],) for item in backend_stage_manifest(context)}
    if context.unit_kind == "frontend" and context.unit_id == "frontend:api-client":
        return {item["task_id"]: tuple(item["requirement_ids"]) for item in frontend_api_task_manifest(context)}
    if context.unit_kind == "page" and context.unit_id.startswith("page:"):
        task_id, expected_kind = f"{context.unit_id}::page", "frontend.page"
    elif context.unit_kind == "frontend" and context.unit_id == "frontend:data:static":
        task_id, expected_kind = f"{context.unit_id}::data-module", "frontend.static_data_module"
    elif context.unit_kind == "backend" and context.unit_id == "backend:bootstrap":
        task_id, expected_kind = "backend:bootstrap::bootstrap", "backend.bootstrap"
    else:
        raise ValueError(f"Unit {context.unit_id} 没有确定性任务身份清单。")
    if any(item.source_refs.get("kind") != expected_kind for item in context.generation_requirements):
        raise ValueError(f"Unit {context.unit_id} 的职责与固定任务分组不一致。")
    return {task_id: tuple(item.requirement_id for item in context.generation_requirements)}
