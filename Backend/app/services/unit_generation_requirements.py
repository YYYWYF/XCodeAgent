"""按 Scope、正式职责和 ReuseFacts 计算本轮缺项，不产生 Candidate 或替换历史任务。"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.planning_frozen import plain_json
from app.services.unit_generation_contracts import GenerationRequirement
from app.services.unit_generation_requirement_targets import (
    endpoint_source_types, exact_id, resource_catalog_fingerprint, responsibility, scoped_formal_targets,
)
from app.services.unit_generation_requirements_contracts import (
    GenerationRequirementsError, UnitGenerationRequirements, fail_requirement_input,
)


_STRUCTURAL_UNITS = {"application:root", "app:integration"}
_FRONTEND_ENDPOINT_KINDS = {"frontend.api_module", "frontend.static_data_module"}


def _endpoint_requirements(kind: str, keys: Sequence[tuple[str, str]]) -> list[GenerationRequirement]:
    """每个正式 Endpoint 对应一个前端实现职责，不跨接口猜测 API 模块等价。"""

    return [responsibility(
        kind, contract_id, endpoint_id, description=f"实现正式接口 {contract_id}/{endpoint_id} 的前端业务访问职责。",
        kind=kind, api_contract_id=contract_id, endpoint_id=endpoint_id,
    ) for contract_id, endpoint_id in sorted(keys)]


def _backend_requirements(key: tuple[str, str], sources: Mapping[str, str]) -> list[GenerationRequirement]:
    """列出 Endpoint 的实体业务层职责；仅划分需求，不创建或规定 Candidate Task 数量。"""

    result = []
    for entity_id, source_type in sorted(sources.items()):
        if source_type == "static":
            continue
        kinds = (
            ("backend.domain_mapping", "backend.repository") if source_type == "database"
            else ("backend.external_api_client", "backend.external_api_mapping")
        )
        for kind in (*kinds, "backend.application_service", "backend.endpoint_controller"):
            result.append(responsibility(
                kind, *key, entity_id, description=f"实现 {key[0]}/{key[1]} 中实体 {entity_id} 的 {kind} 职责。",
                kind=kind, api_contract_id=key[0], endpoint_id=key[1], entity_id=entity_id,
                data_source_type=source_type,
            ))
    return result


def _unit_responsibilities(
    unit_id: str, pages: Mapping, endpoints: Mapping, sources: Mapping, plan: dict,
) -> list[GenerationRequirement]:
    """由 Unit 的正式身份选择职责规则；未知 Unit 不能默认为任意模型任务。"""

    if unit_id in _STRUCTURAL_UNITS or unit_id == "frontend:shell":
        return []
    if unit_id.startswith("page:"):
        page_id = unit_id.removeprefix("page:")
        if page_id not in pages:
            fail_requirement_input("GENERATION_UNIT_OUTSIDE_SCOPE", f"页面 Unit {unit_id} 不在当前正式 Scope 内。", unit_ids=[unit_id])
        return [responsibility(
            "frontend.page", page_id, description=f"实现页面 {page_id} 的当前正式 PageImplementationContract。",
            kind="frontend.page", page_id=page_id,
        )]
    real_keys = [key for key in endpoints if set(sources[key].values()) & {"database", "external_api"}]
    if unit_id == "frontend:api-client":
        if not real_keys:
            return []
        return [responsibility(
            "frontend.response-entity-adapter", description="提供统一 ResponseEntity 传输适配器，供业务 API 模块复用。",
            kind="frontend.shared_capability", target_id="response-entity-adapter",
        ), *_endpoint_requirements("frontend.api_module", real_keys)]
    if unit_id.startswith("frontend:data:"):
        source_id = unit_id.removeprefix("frontend:data:")
        if source_id != "static":
            fail_requirement_input("GENERATION_UNIT_UNSUPPORTED", f"当前正式静态数据 Unit 身份必须为 frontend:data:static，收到 {unit_id}。", unit_ids=[unit_id])
        return _endpoint_requirements("frontend.static_data_module", [key for key in endpoints if "static" in sources[key].values()])
    if unit_id == "backend:bootstrap":
        source_types = {source for mapping in sources.values() for source in mapping.values()} & {"database", "external_api"}
        return [responsibility(
            "backend.bootstrap", source_type, description=f"提供 {source_type} 数据源所需的后端公共基础能力。",
            kind="backend.bootstrap", data_source_type=source_type,
        ) for source_type in sorted(source_types)]
    if unit_id.startswith("backend:endpoint:"):
        matches = [key for key in endpoints if unit_id == f"backend:endpoint:{key[0]}:{key[1]}"]
        if len(matches) != 1:
            fail_requirement_input("GENERATION_UNIT_OUTSIDE_SCOPE", f"Endpoint Unit {unit_id} 不在 Scope 内或身份有歧义。", unit_ids=[unit_id])
        return _backend_requirements(matches[0], sources[matches[0]])
    if unit_id == "frontend:auth-guard":
        fingerprint = resource_catalog_fingerprint(plan)
        return [] if fingerprint is None else [responsibility(
            "frontend.auth.resources", fingerprint, description="将当前已确认的完整资源目录物化到 resources.ts。",
            kind="frontend.auth.resources", resource_catalog_fingerprint=fingerprint,
            paths=["frontend/src/constants/resources.ts"],
        )]
    fail_requirement_input("GENERATION_UNIT_UNSUPPORTED", f"Unit {unit_id} 尚无明确的生成职责规则。", unit_ids=[unit_id])


def _is_satisfied(unit_id: str, requirement: GenerationRequirement, facts: ReuseFacts) -> bool:
    """仅匹配精确 capability 或正式前端 Endpoint owner，不检查 Task 数量和执行状态。"""

    capability = requirement.requirement_id
    if facts.reusable_capabilities_by_unit.get(unit_id, {}).get(capability):
        return True
    if any(item.unit_id == unit_id and item.capability_id == capability for item in facts.external_capabilities):
        return True
    refs = requirement.source_refs
    if refs.get("kind") in _FRONTEND_ENDPOINT_KINDS:
        return any(
            owner.api_contract_id == refs["api_contract_id"] and owner.endpoint_id == refs["endpoint_id"]
            for owner in facts.retained_endpoint_owners
        )
    return False


def resolve_generation_requirements(
    *, required_unit_ids: Sequence[str], build_execution_scope: Mapping[str, Any],
    unit_skeleton: Mapping[str, Any], reuse_facts: ReuseFacts, formal_target: Mapping[str, Any],
) -> UnitGenerationRequirements:
    """计算当前 Scope 的新增职责、策略和 planning 集合；正式 target 为完整 TechnicalPlan 投影。

    本函数只读输入，异常携带 T1.1 issues。未知职责和冲突基线显式失败；空需求不调度。
    共享 Unit 可保留全部历史 Task 并仅返回缺项，绝不产生 replacement requirement。
    """

    facts = ReuseFacts.model_validate(reuse_facts)
    if facts.issues:
        raise GenerationRequirementsError(facts.issues)
    if not isinstance(required_unit_ids, (list, tuple)):
        fail_requirement_input("REQUIRED_UNITS_INVALID", "required_unit_ids 必须为明确的 ID 数组。")
    required = sorted({exact_id(unit_id, "required_unit_ids") for unit_id in required_unit_ids})
    units = unit_skeleton.get("build_units")
    if not isinstance(units, Mapping) or any(unit_id not in units for unit_id in required):
        fail_requirement_input("REQUIRED_UNIT_MISSING", "required Unit 必须存在于当前 Unit Skeleton。", unit_ids=required)
    for unit_id in required:
        if not isinstance(units[unit_id], Mapping) or units[unit_id].get("id") != unit_id:
            fail_requirement_input("GENERATION_UNIT_IDENTITY_INVALID", "Unit Skeleton 的 key 与节点身份必须一致。", unit_ids=[unit_id], category="platform")
    if not isinstance(formal_target, Mapping) or not isinstance(build_execution_scope, Mapping):
        fail_requirement_input("FORMAL_GENERATION_INPUT_INVALID", "formal_target 与 BuildExecutionScope 必须为明确对象。")
    plan = plain_json(formal_target)
    pages, endpoints = scoped_formal_targets(plan, build_execution_scope)
    sources = endpoint_source_types(plan, endpoints)
    requirements_by_unit = {}
    strategies = {}
    for unit_id in required:
        duties = _unit_responsibilities(unit_id, pages, endpoints, sources, plan)
        missing = [item for item in duties if not _is_satisfied(unit_id, item, facts)]
        if unit_id in _STRUCTURAL_UNITS:
            strategy = "structural_only"
        elif unit_id == "frontend:shell":
            strategy = "prerequisite_only"
            if not any(item.unit_id == unit_id and item.capability_id == "frontend.shell.ready" for item in facts.external_capabilities):
                fail_requirement_input("SHELL_PREREQUISITE_MISSING", "frontend:shell 缺少平台已验证的模板前置能力。", unit_ids=[unit_id])
        elif duties and not missing:
            strategy = "reuse_only"
        else:
            strategy = "deterministic" if unit_id == "frontend:auth-guard" else "model"
        requirements_by_unit[unit_id] = sorted(missing, key=lambda item: item.requirement_id)
        strategies[unit_id] = strategy
    return UnitGenerationRequirements(
        generation_requirements_by_unit=requirements_by_unit,
        generation_strategy_by_unit=strategies,
        planning_unit_ids=[unit_id for unit_id in required if requirements_by_unit[unit_id] and strategies[unit_id] in {"model", "deterministic"}],
    )
