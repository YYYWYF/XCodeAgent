"""由 Unit 职责和 Frozen Store 独立编译期望正式来源绑定清单。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domain.models import BuildUnitKind
from app.services.frozen_contract_catalog import (
    ContractCatalogBindingError,
    FormalContractSourceRef,
    canonicalize_formal_source_refs,
)
from app.services.frozen_contract_manifest_index import (
    api_selectors,
    authorization_matches,
    authorization_targets,
    contracts_by_identity,
    endpoint_index as build_endpoint_index,
    endpoint_api_design_index,
    endpoint_api_design_source_types,
    exact_manifest_id,
    manifest_sequence,
    requirement_record,
    technical_contract,
)
from app.services.frozen_contract_store import FrozenContract, FrozenContractStore
from app.services.planning_frozen import plain_json


_ENDPOINT_REQUIREMENT_KINDS = {
    "frontend.api_module",
    "frontend.static_data_module",
    "backend.objects",
    "backend.repository",
    "backend.upstream",
    "backend.application_service",
    "backend.endpoint_controller",
}
_SCOPED_REQUIREMENT_KINDS = {
    "frontend.shared_capability",
    "backend.bootstrap",
    "frontend.auth.resources",
}


def _endpoint_keys_for_requirement(
    *,
    unit_id: str,
    unit_kind: BuildUnitKind,
    source_refs: Mapping[str, Any],
    page_contracts: Mapping[str, FrozenContract],
    endpoint_index: Mapping[tuple[str, str], tuple[FrozenContract, int]],
    scoped_endpoint_keys: set[tuple[str, str]],
) -> tuple[str | None, set[tuple[str, str]]]:
    """按职责类别选择 Page 或精确/Scope Endpoint，不扩大到其他 Scope。"""

    requirement_kind = exact_manifest_id(source_refs.get("kind"), "generation requirement kind")
    if requirement_kind == "frontend.page":
        page_id = exact_manifest_id(source_refs.get("page_id"), "frontend.page.page_id")
        page_contract = page_contracts.get(page_id)
        if page_contract is None:
            raise ContractCatalogBindingError(f"Page {page_id} 缺少 page_contract。")
        endpoint_ids = {
            exact_manifest_id(item, f"Page {page_id}.requiredEndpointIds")
            for item in manifest_sequence(
                page_contract.content.get("requiredEndpointIds"),
                f"Page {page_id}.requiredEndpointIds",
            )
        }
        keys = {
            key
            for endpoint_id in endpoint_ids
            for key in scoped_endpoint_keys
            if key[1] == endpoint_id
        }
        if any(sum(key[1] == endpoint_id for key in keys) != 1 for endpoint_id in endpoint_ids):
            raise ContractCatalogBindingError(
                f"Page {page_id} 的 required Endpoint 缺失或 API 归属不唯一。"
            )
        if unit_kind != "page" or unit_id != f"page:{page_id}":
            raise ContractCatalogBindingError(
                f"Page requirement {page_id} 与 Unit {unit_id} 身份不一致。"
            )
        return page_id, keys
    if requirement_kind in _ENDPOINT_REQUIREMENT_KINDS:
        key = (
            exact_manifest_id(source_refs.get("api_contract_id"), "requirement.api_contract_id"),
            exact_manifest_id(source_refs.get("endpoint_id"), "requirement.endpoint_id"),
        )
        if key not in endpoint_index or key not in scoped_endpoint_keys:
            raise ContractCatalogBindingError(
                f"职责引用的 Endpoint {key[0]}/{key[1]} 不属于当前正式 Scope。"
            )
        return None, {key}
    if requirement_kind in _SCOPED_REQUIREMENT_KINDS:
        return None, set(scoped_endpoint_keys)
    raise ContractCatalogBindingError(
        f"generation requirement kind {requirement_kind} 缺少正式绑定规则。"
    )


def compile_expected_unit_formal_source_refs(
    *,
    unit_id: str,
    unit_kind: BuildUnitKind,
    generation_requirements: Sequence[Any],
    scoped_endpoint_keys: Sequence[tuple[str, str]],
    frozen_contract_store: FrozenContractStore,
) -> tuple[FormalContractSourceRef, ...]:
    """独立编译每项职责必须具备的完整 Frozen Store binding manifest。"""

    if not isinstance(frozen_contract_store, FrozenContractStore):
        raise ContractCatalogBindingError("期望清单只接受已验证的 FrozenContractStore instance。")
    store = frozen_contract_store
    technical = technical_contract(store)
    pages = contracts_by_identity(store, kind="page_contract", identity_field="pageId")
    apis = contracts_by_identity(store, kind="api_contract", identity_field="id")
    endpoint_api_designs = endpoint_api_design_index(store)
    endpoints = build_endpoint_index(apis)
    authorization_contracts = tuple(
        item for item in store.contracts.values() if item.kind == "authorization_slice"
    )
    required_page_ids, required_endpoint_ids = authorization_targets(technical)
    scoped_keys = set(scoped_endpoint_keys)
    if any(key not in endpoints for key in scoped_keys):
        raise ContractCatalogBindingError("当前 Scope 含 Frozen Store 中不存在的 Endpoint。")

    grants: dict[tuple[str, str], tuple[FrozenContract, set[str]]] = {}

    def grant(requirement_id: str, contract: FrozenContract, selectors: set[str]) -> None:
        """按 requirement/ref 合并 selector，最终仍保留职责级期望关系。"""

        key = (requirement_id, contract.ref_id)
        if key not in grants:
            grants[key] = (contract, set())
        grants[key][1].update(selectors)

    for raw_requirement in generation_requirements:
        requirement_id, source_refs = requirement_record(raw_requirement)
        grant(requirement_id, technical, {"/architecture"})
        page_id, relevant_keys = _endpoint_keys_for_requirement(
            unit_id=unit_id,
            unit_kind=unit_kind,
            source_refs=source_refs,
            page_contracts=pages,
            endpoint_index=endpoints,
            scoped_endpoint_keys=scoped_keys,
        )
        if page_id is not None:
            grant(requirement_id, pages[page_id], {"/"})

        requirement_kind = source_refs["kind"]
        source_type = source_refs.get("data_source_type")
        if requirement_kind == "backend.bootstrap":
            source_type = exact_manifest_id(source_type, "backend.bootstrap.data_source_type")
            relevant_keys = {
                key
                for key in relevant_keys
                if source_type in endpoint_api_design_source_types(
                    _required_endpoint_api_design(endpoint_api_designs, key)
                )
            }

        indexes_by_contract: dict[str, set[int]] = {}
        for key in relevant_keys:
            contract, endpoint_index = endpoints[key]
            indexes_by_contract.setdefault(contract.ref_id, set()).add(endpoint_index)
        for ref_id, endpoint_indexes in indexes_by_contract.items():
            contract = store[ref_id]
            grant(requirement_id, contract, api_selectors(contract, endpoint_indexes))
            for key in relevant_keys:
                if key[0] != contract.content.get("id"):
                    continue
                endpoint_api_design = _required_endpoint_api_design(endpoint_api_designs, key)
                grant(requirement_id, endpoint_api_design, {"/"})

        relevant_endpoint_ids = {key[1] for key in relevant_keys}
        for contract in authorization_matches(
            authorization_contracts,
            page_id=page_id,
            endpoint_ids=relevant_endpoint_ids,
            required_page_ids=required_page_ids,
            required_endpoint_ids=required_endpoint_ids,
        ):
            grant(requirement_id, contract, {"/"})

    bindings = tuple(
        FormalContractSourceRef(
            unit_id=unit_id,
            unit_kind=unit_kind,
            requirement_ids=(requirement_id,),
            kind=contract.kind,
            source=plain_json(contract.source),
            selectors=tuple(selectors),
        )
        for (requirement_id, _), (contract, selectors) in grants.items()
    )
    return canonicalize_formal_source_refs(bindings)


def _required_endpoint_api_design(
    designs: Mapping[tuple[str, str], FrozenContract],
    key: tuple[str, str],
) -> FrozenContract:
    """读取 Scope 所需的 Endpoint API Design，缺失时 fail closed。"""

    design = designs.get(key)
    if design is None:
        raise ContractCatalogBindingError(
            f"Endpoint {key[0]}/{key[1]} 缺少 endpoint_api_design。"
        )
    return design
