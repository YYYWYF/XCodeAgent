"""从当前正式 TechnicalPlan 和 Scope 提取职责目标，不读取文件或规划历史 Task。"""

from collections.abc import Mapping
from hashlib import sha256
import json
from typing import Any
from urllib.parse import quote

from app.services.authorization_frontend_projection import compile_frontend_authorization_projection
from app.services.unit_generation_contracts import GenerationRequirement
from app.services.unit_generation_requirements_contracts import fail_requirement_input


def exact_id(value: Any, label: str) -> str:
    """读取正式身份，拒绝空值和隐式转换，不修剪或自动补 ID。"""

    if not isinstance(value, str) or not value or value != value.strip():
        fail_requirement_input("FORMAL_GENERATION_IDENTITY_INVALID", f"{label} 缺少精确非空身份。")
    return value


def object_index(value: Any, key: str, label: str) -> dict[str, dict]:
    """建立完整正式对象索引，重复身份或非法项目显式失败，不静默取第一项。"""

    if not isinstance(value, list):
        fail_requirement_input("FORMAL_GENERATION_INPUT_INVALID", f"{label} 必须为数组。")
    result = {}
    for item in value:
        if not isinstance(item, dict):
            fail_requirement_input("FORMAL_GENERATION_INPUT_INVALID", f"{label} 项必须为对象。")
        identity = exact_id(item.get(key), label)
        if identity in result:
            fail_requirement_input("FORMAL_GENERATION_IDENTITY_CONFLICT", f"{label} 存在重复身份 {identity}。")
        result[identity] = item
    return result


def scoped_formal_targets(plan: dict, scope: Mapping) -> tuple[dict[str, dict], dict[tuple[str, str], dict]]:
    """按 application/page/endpoint Scope 选择完整正式目标，拒绝模糊 Endpoint 归属。"""

    if plan.get("confirmation_status") != "confirmed":
        fail_requirement_input("FORMAL_GENERATION_INPUT_UNCONFIRMED", "生成职责必须来自已确认的正式 TechnicalPlan。")
    scope_type = scope.get("type")
    target_id = exact_id(scope.get("targetId"), "BuildExecutionScope.targetId")
    if scope_type not in {"application", "page", "endpoint"}:
        fail_requirement_input("GENERATION_SCOPE_UNSUPPORTED", "当前生成职责仅支持 application/page/endpoint Scope。")
    pages = object_index(plan.get("page_implementation_contracts", []), "pageId", "PageImplementationContract")
    contracts = object_index(plan.get("api_contracts", []), "id", "API Contract")
    endpoints = {
        (contract_id, endpoint_id): {**endpoint, "api_contract_id": contract_id}
        for contract_id, contract in contracts.items()
        for endpoint_id, endpoint in object_index(contract.get("endpoints"), "id", "Endpoint").items()
    }
    if scope_type == "application":
        if target_id != "application":
            fail_requirement_input("GENERATION_SCOPE_MISMATCH", "application Scope 的 targetId 必须为 application。")
        return pages, endpoints
    if scope_type == "endpoint":
        key = (exact_id(scope.get("apiContractId"), "BuildExecutionScope.apiContractId"), target_id)
        if key not in endpoints:
            fail_requirement_input("FORMAL_GENERATION_TARGET_MISSING", f"正式目录缺少 Endpoint {key}。")
        return {}, {key: endpoints[key]}
    if target_id not in pages:
        fail_requirement_input("FORMAL_GENERATION_TARGET_MISSING", f"正式目录缺少页面实现契约 {target_id}。")
    required_endpoints = pages[target_id].get("requiredEndpointIds")
    if not isinstance(required_endpoints, list):
        fail_requirement_input("FORMAL_GENERATION_INPUT_INVALID", "PageImplementationContract.requiredEndpointIds 必须为数组。")
    selected = {}
    for endpoint_id in required_endpoints:
        endpoint_id = exact_id(endpoint_id, "requiredEndpointIds")
        matches = [key for key in endpoints if key[1] == endpoint_id]
        if len(matches) != 1:
            fail_requirement_input("FORMAL_GENERATION_ENDPOINT_AMBIGUOUS", f"页面引用 Endpoint {endpoint_id} 缺失或无法唯一确定 API Contract。")
        selected[matches[0]] = endpoints[matches[0]]
    return {target_id: pages[target_id]}, selected


def endpoint_source_types(plan: dict, endpoints: Mapping) -> dict[tuple[str, str], dict[str, str]]:
    """按正式合同读取已确认实体的数据源类型；缺失类型不能默认为 database。"""

    contracts = object_index(plan.get("api_contracts", []), "id", "API Contract")
    designs = object_index(plan.get("entity_detail_plans", []), "entity_id", "EntitySourceBinding")
    result = {}
    for key in sorted(endpoints):
        entity_ids = contracts[key[0]].get("entity_ids")
        if not isinstance(entity_ids, list) or not entity_ids:
            fail_requirement_input("GENERATION_ENTITY_BINDING_MISSING", f"Endpoint {key} 缺少正式实体绑定。")
        sources = {}
        for entity_id in entity_ids:
            entity_id = exact_id(entity_id, "API Contract.entity_ids")
            design = designs.get(entity_id, {})
            source_type = design.get("data_source_type")
            if design.get("status") != "confirmed" or source_type not in {"database", "external_api", "static"}:
                fail_requirement_input("GENERATION_ENTITY_BINDING_MISSING", f"实体 {entity_id} 缺少已确认且类型明确的数据源绑定。")
            sources[entity_id] = source_type
        result[key] = sources
    return result


def resource_catalog_fingerprint(plan: dict) -> str | None:
    """仅对现有投影的完整资源目录计算 SHA-256；不把页面路由放入资源身份。"""

    if plan.get("confirmation_status") != "confirmed":
        fail_requirement_input("AUTH_RESOURCE_INPUT_UNCONFIRMED", "资源目录指纹必须来自已确认的正式 TechnicalPlan。")
    manifest = plan.get("authorization_manifest")
    if manifest is None:
        return None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("enabled"), bool):
        fail_requirement_input("AUTH_RESOURCE_INPUT_INVALID", "正式 authorization_manifest.enabled 必须为布尔值。")
    if not manifest["enabled"]:
        return None
    resources = object_index(manifest.get("resources"), "resourceKey", "Authorization resource")
    if not resources:
        fail_requirement_input("AUTH_RESOURCE_INPUT_INVALID", "已启用权限的正式资源目录不能为空。")
    try:
        projection = compile_frontend_authorization_projection(plan)
    except ValueError as exc:
        fail_requirement_input("AUTH_RESOURCE_INPUT_INVALID", str(exc), unit_ids=["frontend:auth-guard"])
    canonical = json.dumps(projection["resources"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def responsibility(responsibility_kind: str, *identities: str, description: str, **source_refs: Any) -> GenerationRequirement:
    """按正式职责类型和精确目标构造稳定 ID，并声明后续 Candidate 必须提供的能力身份。"""

    requirement_id = ":".join([responsibility_kind, *(quote(item, safe="") for item in identities)])
    return GenerationRequirement(
        requirement_id=requirement_id, description=description,
        source_refs={"artifact": "technical-plan", "capability_id": requirement_id, **source_refs},
    )
