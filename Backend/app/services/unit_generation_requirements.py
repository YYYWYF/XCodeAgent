"""按 Scope、正式职责和 ReuseFacts 计算本轮缺项，不产生 Candidate 或替换历史任务。"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.planning_frozen import plain_json
from app.services.unit_generation_contracts import GenerationRequirement
from app.services.unit_generation_requirement_targets import (
    ENDPOINT_PHYSICAL_SOURCE_TYPES, endpoint_source_types, exact_id,
    resource_catalog_fingerprint, responsibility, scoped_formal_targets,
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


def _backend_requirements(
    key: tuple[str, str],
    source_types: Sequence[str],
) -> list[GenerationRequirement]:
    """按 Endpoint 及物理来源集合列出职责，不按实体拆分职责或独立映射阶段。"""

    physical_types = tuple(sorted(set(source_types) & ENDPOINT_PHYSICAL_SOURCE_TYPES))
    shared_refs = {
        "artifact": "endpoint-api-design",
        "api_contract_id": key[0],
        "endpoint_id": key[1],
        "target_id": key[1],
        "data_source_types": list(physical_types),
    }
    result = [responsibility(
        "backend.endpoint.objects", *key,
        description=f"实现 Endpoint {key[0]}/{key[1]} 的请求、响应及内部对象职责。",
        kind="backend.objects", **shared_refs,
    )]
    if "database" in physical_types:
        result.append(responsibility(
            "backend.repository", *key,
            description=f"实现 Endpoint {key[0]}/{key[1]} 的数据库访问职责。",
            kind="backend.repository", **shared_refs,
        ))
    if "external_api" in physical_types:
        result.append(responsibility(
            "backend.upstream", *key,
            description=f"实现 Endpoint {key[0]}/{key[1]} 的上游 Client、传输合同及来源快照职责。",
            kind="backend.upstream", **shared_refs,
        ))
    result.extend([
        responsibility(
            "backend.application_service", *key,
            description=(
                f"实现 Endpoint {key[0]}/{key[1]} 的字段映射、多来源组合、"
                "业务说明及响应转换职责。"
            ),
            kind="backend.application_service", **shared_refs,
        ),
        responsibility(
            "backend.endpoint_controller", *key,
            description=f"实现 Endpoint {key[0]}/{key[1]} 的内部接口适配职责。",
            kind="backend.endpoint_controller", **shared_refs,
        ),
    ])
    return result


def _unit_responsibilities(
    unit_id: str, pages: Mapping, endpoints: Mapping,
    source_types_by_endpoint: Mapping[tuple[str, str], Sequence[str]], plan: dict,
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
    real_keys = [
        key for key in endpoints
        if set(source_types_by_endpoint[key]) & ENDPOINT_PHYSICAL_SOURCE_TYPES
    ]
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
        return _endpoint_requirements(
            "frontend.static_data_module",
            [key for key in endpoints if not source_types_by_endpoint[key]],
        )
    if unit_id == "backend:bootstrap":
        source_types = {
            source_type
            for endpoint_types in source_types_by_endpoint.values()
            for source_type in endpoint_types
        } & ENDPOINT_PHYSICAL_SOURCE_TYPES
        return [responsibility(
            "backend.bootstrap", source_type,
            artifact="endpoint-api-design",
            description=f"提供 {source_type} 数据源所需的后端公共基础能力。",
            kind="backend.bootstrap", data_source_type=source_type,
        ) for source_type in sorted(source_types)]
    if unit_id.startswith("backend:endpoint:"):
        matches = [key for key in endpoints if unit_id == f"backend:endpoint:{key[0]}:{key[1]}"]
        if len(matches) != 1:
            fail_requirement_input("GENERATION_UNIT_OUTSIDE_SCOPE", f"Endpoint Unit {unit_id} 不在 Scope 内或身份有歧义。", unit_ids=[unit_id])
        return _backend_requirements(matches[0], source_types_by_endpoint[matches[0]])
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


def _requires_endpoint_source_types(required_unit_ids: Sequence[str], endpoints: Mapping) -> bool:
    """判断当前职责集合是否真的需要读取 Endpoint Design 的来源映射。"""

    if not endpoints:
        return False
    return any(
        unit_id in {"frontend:api-client", "frontend:data:static", "backend:bootstrap"}
        or unit_id.startswith("backend:endpoint:")
        for unit_id in required_unit_ids
    )


def resolve_generation_requirements(
    *, required_unit_ids: Sequence[str], build_execution_scope: Mapping[str, Any],
    unit_skeleton: Mapping[str, Any], reuse_facts: ReuseFacts, formal_target: Mapping[str, Any],
    endpoint_designs: Sequence[Mapping[str, Any]] = (),
) -> UnitGenerationRequirements:
    """计算当前 Scope 的新增职责、策略和 planning 集合；source type 只来自 Endpoint Design。

    本函数只读输入，异常携带 T1.1 issues。TechnicalPlan 的 entity_ids 只保留业务语义，
    不参与职责数量、阶段、数据源或 target_id 决策；未知职责和冲突基线显式失败。
    空需求不调度，共享 Unit 可保留全部历史 Task 并仅返回缺项，绝不产生 replacement requirement。
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
    sources = (
        endpoint_source_types(endpoint_designs, endpoints)
        if _requires_endpoint_source_types(required, endpoints)
        else {key: frozenset() for key in endpoints}
    )
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
