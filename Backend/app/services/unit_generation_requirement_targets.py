"""从当前正式 TechnicalPlan、Endpoint API Design 和 Scope 提取职责目标。"""

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from app.services.authorization_resource_catalog import (
    compile_frontend_resource_catalog,
    resource_catalog_fingerprint as fingerprint_catalog,
)
from app.services.unit_generation_contracts import GenerationRequirement
from app.services.unit_generation_requirements_contracts import fail_requirement_input


def exact_id(value: Any, label: str) -> str:
    """读取正式身份，拒绝空值和隐式转换，不修剪或自动补 ID。"""

    if not isinstance(value, str) or not value or value != value.strip():
        fail_requirement_input("FORMAL_GENERATION_IDENTITY_INVALID", f"{label} 缺少精确非空身份。")
    return value


def object_index(value: Any, key: str, label: str) -> dict[str, Mapping[str, Any]]:
    """建立完整正式对象索引，重复身份或非法项目显式失败，不静默取第一项。"""

    if not isinstance(value, (list, tuple)):
        fail_requirement_input("FORMAL_GENERATION_INPUT_INVALID", f"{label} 必须为数组。")
    result = {}
    for item in value:
        if not isinstance(item, Mapping):
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


ENDPOINT_PHYSICAL_SOURCE_TYPES = frozenset({"database", "external_api"})


def endpoint_source_types(
    endpoint_designs: Any,
    endpoints: Mapping,
) -> dict[tuple[str, str], frozenset[str]]:
    """从完整 Endpoint API Design 的 fieldMappings 派生每个 Endpoint 的物理来源集合。"""

    designs: dict[tuple[str, str], Mapping[str, Any]] = {}
    if not isinstance(endpoint_designs, (list, tuple)):
        fail_requirement_input("FORMAL_GENERATION_INPUT_INVALID", "Endpoint API Design 必须为数组。")
    for design in endpoint_designs:
        if not isinstance(design, Mapping):
            fail_requirement_input("FORMAL_GENERATION_INPUT_INVALID", "Endpoint API Design 项必须为对象。")
        api_contract_id = exact_id(
            design.get("apiContractId"),
            "Endpoint API Design.apiContractId",
        )
        endpoint_id = exact_id(
            design.get("endpointId"),
            "Endpoint API Design.endpointId",
        )
        key = (api_contract_id, endpoint_id)
        if key in designs:
            fail_requirement_input(
                "FORMAL_GENERATION_IDENTITY_CONFLICT",
                f"Endpoint API Design 存在重复身份 {api_contract_id}/{endpoint_id}。",
            )
        designs[key] = design

    result: dict[tuple[str, str], frozenset[str]] = {}
    for key in sorted(endpoints):
        design = designs.get(key)
        if design is None:
            fail_requirement_input(
                "GENERATION_ENDPOINT_API_DESIGN_MISSING",
                f"Endpoint {key[0]}/{key[1]} 缺少当前 Endpoint API Design。",
            )
        field_mappings = design.get("fieldMappings")
        if not isinstance(field_mappings, (list, tuple)):
            fail_requirement_input(
                "FORMAL_GENERATION_INPUT_INVALID",
                f"Endpoint {key[0]}/{key[1]} 的 fieldMappings 必须为数组。",
            )
        source_types: set[str] = set()
        for mapping in field_mappings:
            if not isinstance(mapping, Mapping):
                fail_requirement_input(
                    "FORMAL_GENERATION_INPUT_INVALID",
                    f"Endpoint {key[0]}/{key[1]} 的 fieldMappings 项必须为对象。",
                )
            mapping_type = mapping.get("mappingType")
            source_fields = mapping.get("sourceFields")
            if source_fields is None:
                if mapping_type == "source_mapping":
                    fail_requirement_input(
                        "FORMAL_GENERATION_INPUT_INVALID",
                        f"Endpoint {key[0]}/{key[1]} 的 source_mapping 缺少 sourceFields。",
                    )
                continue
            if not isinstance(source_fields, (list, tuple)):
                fail_requirement_input(
                    "FORMAL_GENERATION_INPUT_INVALID",
                    f"Endpoint {key[0]}/{key[1]} 的 sourceFields 必须为数组。",
                )
            if mapping_type == "source_mapping" and not source_fields:
                fail_requirement_input(
                    "FORMAL_GENERATION_INPUT_INVALID",
                    f"Endpoint {key[0]}/{key[1]} 的 source_mapping 不得为空。",
                )
            for source in source_fields:
                if not isinstance(source, Mapping):
                    fail_requirement_input(
                        "FORMAL_GENERATION_INPUT_INVALID",
                        f"Endpoint {key[0]}/{key[1]} 的 sourceFields 项必须为对象。",
                    )
                source_type = source.get("sourceType")
                if not isinstance(source_type, str) or source_type not in ENDPOINT_PHYSICAL_SOURCE_TYPES:
                    fail_requirement_input(
                        "GENERATION_ENDPOINT_SOURCE_TYPE_INVALID",
                        f"Endpoint {key[0]}/{key[1]} 含不受支持的 sourceType：{source_type!r}。",
                    )
                source_types.add(source_type)
        result[key] = frozenset(source_types)
    return result


def resource_catalog_fingerprint(plan: dict) -> str | None:
    """校验正式确认门禁后委托完整资源目录计算身份，不依赖页面路由编译。"""

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
        catalog = compile_frontend_resource_catalog(manifest)
    except ValueError as exc:
        fail_requirement_input("AUTH_RESOURCE_INPUT_INVALID", str(exc), unit_ids=["frontend:auth-guard"])
    return fingerprint_catalog(catalog)


def responsibility(responsibility_kind: str, *identities: str, description: str, **source_refs: Any) -> GenerationRequirement:
    """按正式职责类型和精确目标构造稳定 ID，并声明后续 Candidate 必须提供的能力身份。"""

    requirement_id = ":".join([responsibility_kind, *(quote(item, safe="") for item in identities)])
    artifact = source_refs.pop("artifact", "technical-plan")
    return GenerationRequirement(
        requirement_id=requirement_id, description=description,
        source_refs={"artifact": artifact, "capability_id": requirement_id, **source_refs},
    )
