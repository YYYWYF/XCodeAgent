"""由冻结职责生成任务身份清单，供 Prompt 和缺失 ID 绑定共同使用。"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from typing import Any
from urllib.parse import quote

from app.services.business_acceptance import canonical_frontend_api_module_path, frontend_api_task_id
from app.services.unit_generation_contracts import GenerationRequirement, UnitGenerationContext


def _text(value: Any) -> str:
    """只接受无需修剪的非空正式身份。"""

    return value if isinstance(value, str) and value and value == value.strip() else ""


_BUSINESS_KINDS = frozenset({
    "backend.objects", "backend.repository", "backend.upstream", "backend.application_service",
})
PHYSICAL_SOURCE_TYPES = frozenset({"database", "external_api"})
BACKEND_MODULE_PREFIX = "backend:module:"
BACKEND_STAGE_LABELS = {
    "foundation": "模块基础骨架",
    "business": "接口业务实现",
    "controller": "模块 Controller",
}


def backend_module_unit_id(contract_id: str) -> str:
    """为 API Contract 派生稳定的后端模块 Unit 身份。"""
    return f"{BACKEND_MODULE_PREFIX}{contract_id}"


def backend_bootstrap_task_id(context: UnitGenerationContext) -> str:
    """按新增来源集合生成基础设施任务身份，避免后续接入另一来源时冲突。"""
    sources = sorted({_text(item.source_refs.get("data_source_type")) for item in context.generation_requirements})
    if not sources or set(sources) - PHYSICAL_SOURCE_TYPES:
        raise ValueError("Bootstrap 缺少有效的物理来源。")
    return f"backend:bootstrap::{'+'.join(sources)}::bootstrap"


def backend_stage_manifest(context: UnitGenerationContext) -> tuple[dict[str, Any], ...]:
    """将模块职责编译为基础骨架、串行单接口业务和统一 Controller 清单。"""
    groups: dict[tuple[str, str], list[GenerationRequirement]] = {}
    for requirement in context.generation_requirements:
        refs = requirement.source_refs
        kind = refs.get("kind")
        endpoint = _text(refs.get("endpoint_id"))
        contract = _text(refs.get("api_contract_id"))
        if not endpoint or not contract or context.unit_id != backend_module_unit_id(contract):
            raise ValueError(f"模块 {context.unit_id} 的职责缺少精确接口身份。")
        if refs.get("artifact") != "endpoint-api-design" or refs.get("target_id") != endpoint:
            raise ValueError("模块职责必须绑定已确认 Endpoint 设计。")
        if kind == "backend.foundation":
            stage, group = "foundation", ""
        elif kind == "backend.endpoint_controller":
            stage, group = "controller", ""
        elif kind in _BUSINESS_KINDS:
            stage, group = "business", endpoint
        else:
            raise ValueError(f"未知后端职责：{kind}")
        groups.setdefault((stage, group), []).append(requirement)
    result: list[dict[str, Any]] = []
    order = {stage: index for index, stage in enumerate(BACKEND_STAGE_LABELS)}
    for (stage, endpoint), requirements in sorted(groups.items(), key=lambda item: (order[item[0][0]], item[0][1])):
        requirements = sorted(requirements, key=lambda item: item.requirement_id)
        requirement_ids = [item.requirement_id for item in requirements]
        suffix = quote(endpoint, safe="") if stage == "business" else sha256(
            json.dumps(requirement_ids, ensure_ascii=False).encode()).hexdigest()[:16]
        result.append({
            "key": f"business:{endpoint}" if stage == "business" else stage,
            "task_id": f"{context.unit_id}::{suffix}::{stage}",
            "stage": stage,
            "api_contract_id": context.unit_id.removeprefix(BACKEND_MODULE_PREFIX),
            "endpoint_ids": sorted({item.source_refs["endpoint_id"] for item in requirements}),
            "requirement_ids": requirement_ids,
            "data_source_types": sorted({value for item in requirements for value in item.source_refs.get("data_source_types", ())}),
            "deliverables": [{"kind": item.source_refs["kind"], "target_id": item.source_refs["endpoint_id"],
                              "provides": [item.requirement_id]} for item in requirements],
        })
    return tuple(result)


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

    if context.unit_kind == "backend" and context.unit_id.startswith(BACKEND_MODULE_PREFIX):
        return {item["task_id"]: tuple(item["requirement_ids"]) for item in backend_stage_manifest(context)}
    if context.unit_kind == "frontend" and context.unit_id == "frontend:api-client":
        return {item["task_id"]: tuple(item["requirement_ids"]) for item in frontend_api_task_manifest(context)}
    if context.unit_kind == "page" and context.unit_id.startswith("page:"):
        task_id, expected_kind = f"{context.unit_id}::page", "frontend.page"
    elif context.unit_kind == "frontend" and context.unit_id == "frontend:data:static":
        task_id, expected_kind = f"{context.unit_id}::data-module", "frontend.static_data_module"
    elif context.unit_kind == "backend" and context.unit_id == "backend:bootstrap":
        task_id, expected_kind = backend_bootstrap_task_id(context), "backend.bootstrap"
    else:
        raise ValueError(f"Unit {context.unit_id} 没有确定性任务身份清单。")
    if any(item.source_refs.get("kind") != expected_kind for item in context.generation_requirements):
        raise ValueError(f"Unit {context.unit_id} 的职责与固定任务分组不一致。")
    return {task_id: tuple(item.requirement_id for item in context.generation_requirements)}
