"""期望合同清单编译器使用的 Frozen Store 严格索引与权限选择。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.services.frozen_contract_catalog import ContractCatalogBindingError
from app.services.frozen_contract_store import ContractKind, FrozenContract, FrozenContractStore


def exact_manifest_id(value: Any, label: str) -> str:
    """读取期望清单所需的精确身份，不修剪或隐式转换。"""

    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractCatalogBindingError(f"{label} 缺少精确非空身份。")
    return value


def manifest_sequence(value: Any, label: str) -> tuple[Any, ...]:
    """读取冻结 JSON 数组，并拒绝结构不明确的正式合同字段。"""

    if not isinstance(value, (list, tuple)):
        raise ContractCatalogBindingError(f"{label} 必须为正式数组。")
    return tuple(value)


def requirement_record(value: Any) -> tuple[str, Mapping[str, Any]]:
    """读取一个 generation requirement 的身份和规则层来源。"""

    requirement_id = (
        value.get("requirement_id")
        if isinstance(value, Mapping)
        else getattr(value, "requirement_id", None)
    )
    source_refs = (
        value.get("source_refs")
        if isinstance(value, Mapping)
        else getattr(value, "source_refs", None)
    )
    requirement_id = exact_manifest_id(requirement_id, "generation requirement")
    if not isinstance(source_refs, Mapping):
        raise ContractCatalogBindingError(
            f"generation requirement {requirement_id} 缺少正式 source_refs。"
        )
    return requirement_id, source_refs


def contracts_by_identity(
    store: FrozenContractStore,
    *,
    kind: ContractKind,
    identity_field: str,
) -> dict[str, FrozenContract]:
    """按正文身份建立单值 Store 索引，重复目标一律 fail closed。"""

    result = {}
    for contract in store.contracts.values():
        if contract.kind != kind:
            continue
        identity = exact_manifest_id(
            contract.content.get(identity_field),
            f"{kind}.{identity_field}",
        )
        if identity in result:
            raise ContractCatalogBindingError(
                f"FrozenContractStore 中 {kind} 目标 {identity} 不唯一。"
            )
        result[identity] = contract
    return result


def technical_contract(store: FrozenContractStore) -> FrozenContract:
    """取得 Store 中唯一的 TechnicalPlan 合同。"""

    matches = [item for item in store.contracts.values() if item.kind == "technical_plan"]
    if len(matches) != 1:
        raise ContractCatalogBindingError("FrozenContractStore 缺少唯一 TechnicalPlan。")
    return matches[0]


def endpoint_index(
    api_contracts: Mapping[str, FrozenContract],
) -> dict[tuple[str, str], tuple[FrozenContract, int]]:
    """建立 API/Endpoint 精确索引，并拒绝合同内重复 Endpoint。"""

    result = {}
    for api_id, contract in api_contracts.items():
        endpoints = manifest_sequence(
            contract.content.get("endpoints"),
            f"API Contract {api_id}.endpoints",
        )
        for index, endpoint in enumerate(endpoints):
            if not isinstance(endpoint, Mapping):
                raise ContractCatalogBindingError(
                    f"API Contract {api_id} 的 endpoint 必须为对象。"
                )
            endpoint_id = exact_manifest_id(
                endpoint.get("id"),
                f"API Contract {api_id}.endpoint",
            )
            key = (api_id, endpoint_id)
            if key in result:
                raise ContractCatalogBindingError(
                    f"API Contract {api_id} 中 Endpoint {endpoint_id} 不唯一。"
                )
            result[key] = (contract, index)
    return result


def api_selectors(contract: FrozenContract, endpoint_indexes: set[int]) -> set[str]:
    """只允许读取 API 身份、实体关系和精确 Endpoint，禁止其他片段泄漏。"""

    selectors = {"/id", "/entity_ids"}
    selectors.update(f"/endpoints/{index}" for index in endpoint_indexes)
    return selectors


def authorization_targets(
    technical: FrozenContract,
) -> tuple[set[str], set[str]]:
    """从已启用的正式权限清单读取确实需要权限切片的 Page 和 Endpoint。"""

    manifest = technical.content.get("authorization_manifest")
    if manifest is None:
        return set(), set()
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("enabled"), bool):
        raise ContractCatalogBindingError("TechnicalPlan authorization_manifest 结构无效。")
    if manifest["enabled"] is False:
        return set(), set()
    bindings = manifest.get("bindings")
    if not isinstance(bindings, Mapping):
        raise ContractCatalogBindingError("已启用权限时必须提供正式 bindings。")
    page_ids = set()
    endpoint_ids = set()
    for field in ("pages", "actions"):
        for item in manifest_sequence(
            bindings.get(field),
            f"authorization_manifest.bindings.{field}",
        ):
            if not isinstance(item, Mapping):
                raise ContractCatalogBindingError(f"权限 {field} binding 必须为对象。")
            page_ids.add(
                exact_manifest_id(item.get("pageId"), f"authorization {field}.pageId")
            )
    for item in manifest_sequence(
        bindings.get("endpoints"),
        "authorization_manifest.bindings.endpoints",
    ):
        if not isinstance(item, Mapping):
            raise ContractCatalogBindingError("权限 endpoint binding 必须为对象。")
        endpoint_ids.add(
            exact_manifest_id(item.get("endpointId"), "authorization endpoint.endpointId")
        )
    return page_ids, endpoint_ids


def authorization_matches(
    authorization_contracts: Sequence[FrozenContract],
    *,
    page_id: str | None,
    endpoint_ids: set[str],
    required_page_ids: set[str],
    required_endpoint_ids: set[str],
) -> tuple[FrozenContract, ...]:
    """选择目标权限切片，并验证所有受保护目标均有正式切片。"""

    matches = set()
    if page_id is not None and page_id in required_page_ids:
        page_matches = [
            contract
            for contract in authorization_contracts
            if contract.content.get("pageId") == page_id
        ]
        if len(page_matches) != 1:
            raise ContractCatalogBindingError(
                f"受保护 Page {page_id} 缺少唯一 authorization_slice。"
            )
        matches.add(page_matches[0].ref_id)
    for endpoint_id in sorted(endpoint_ids & required_endpoint_ids):
        endpoint_matches = []
        for contract in authorization_contracts:
            endpoints = manifest_sequence(
                contract.content.get("endpoints"),
                "authorization_slice.endpoints",
            )
            if any(
                isinstance(item, Mapping) and item.get("endpointId") == endpoint_id
                for item in endpoints
            ):
                endpoint_matches.append(contract)
        if not endpoint_matches:
            raise ContractCatalogBindingError(
                f"受保护 Endpoint {endpoint_id} 缺少 authorization_slice。"
            )
        matches.update(item.ref_id for item in endpoint_matches)
    return tuple(
        contract for contract in authorization_contracts if contract.ref_id in matches
    )
