"""按 Unit 正式来源绑定构建 Frozen Contract Catalog / Allowlist。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from pydantic import BeforeValidator, StringConstraints, model_validator

from app.domain.models import BuildUnitKind
from app.services.frozen_contract_store import (
    ContractKind,
    FrozenContract,
    FrozenContractStore,
)
from app.services.planning_frozen import (
    FrozenJsonObject,
    FrozenPlanningModel,
    tuple_input,
)


_Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
_Selector = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
_Identifiers = Annotated[tuple[_Identifier, ...], BeforeValidator(tuple_input)]
_Selectors = Annotated[tuple[_Selector, ...], BeforeValidator(tuple_input)]


class FormalContractSourceRef(FrozenPlanningModel):
    """把一个 Unit 的正式职责绑定到 Store 来源及允许读取的 selector。"""

    unit_id: _Identifier
    unit_kind: BuildUnitKind
    requirement_ids: _Identifiers
    kind: ContractKind
    source: FrozenJsonObject
    selectors: _Selectors

    @model_validator(mode="after")
    def validate_binding(self) -> "FormalContractSourceRef":
        """拒绝空、重复或顺序不稳定的职责和 selector 声明。"""

        if not self.requirement_ids or len(set(self.requirement_ids)) != len(self.requirement_ids):
            raise ValueError("正式来源绑定必须包含互不重复的 requirement_ids。")
        if not self.selectors or len(set(self.selectors)) != len(self.selectors):
            raise ValueError("正式来源绑定必须包含互不重复的 selectors。")
        if not self.source:
            raise ValueError("正式来源绑定必须提供非空 structured source。")
        return self


class ContractCatalogEntry(FrozenPlanningModel):
    """向单个 Unit 暴露的 Store 引用与 selector allowlist。"""

    ref_id: _Identifier
    kind: ContractKind
    selectors: _Selectors

    @model_validator(mode="after")
    def validate_selectors(self) -> "ContractCatalogEntry":
        """Catalog selector 必须非空、唯一且按稳定顺序保存。"""

        if not self.selectors or tuple(sorted(set(self.selectors))) != self.selectors:
            raise ValueError("contract catalog selectors 必须非空、唯一并按字典序排列。")
        return self


class ContractCatalogBindingError(ValueError):
    """表示当前 Unit 的正式来源无法安全绑定到 Frozen Store。"""


def _requirement_record(value: Any) -> tuple[str, Mapping[str, Any]]:
    """从冻结 DTO 或普通映射读取精确职责身份和正式 source_refs。"""

    requirement_id = (
        value.get("requirement_id")
        if isinstance(value, Mapping)
        else getattr(value, "requirement_id", None)
    )
    if (
        not isinstance(requirement_id, str)
        or not requirement_id
        or requirement_id != requirement_id.strip()
    ):
        raise ContractCatalogBindingError("generation requirement 缺少精确 requirement_id。")
    source_refs = (
        value.get("source_refs")
        if isinstance(value, Mapping)
        else getattr(value, "source_refs", None)
    )
    if not isinstance(source_refs, Mapping):
        raise ContractCatalogBindingError(
            f"generation requirement {requirement_id} 缺少正式 source_refs。"
        )
    return requirement_id, source_refs


def _authorized_targets(
    requirements: Mapping[str, Mapping[str, Any]],
    store: FrozenContractStore,
) -> tuple[set[str], set[str], set[str], set[str]]:
    """由当前职责及 Store 内关系推导允许的 page、API、endpoint 和 entity 身份。"""

    page_ids = {
        value.get("page_id")
        for value in requirements.values()
        if isinstance(value.get("page_id"), str)
    }
    api_ids = {
        value.get("api_contract_id")
        for value in requirements.values()
        if isinstance(value.get("api_contract_id"), str)
    }
    endpoint_ids = {
        value.get("endpoint_id")
        for value in requirements.values()
        if isinstance(value.get("endpoint_id"), str)
    }
    entity_ids = {
        value.get("entity_id")
        for value in requirements.values()
        if isinstance(value.get("entity_id"), str)
    }
    for contract in store.contracts.values():
        if contract.kind == "page_contract" and contract.content.get("pageId") in page_ids:
            required = contract.content.get("requiredEndpointIds", ())
            if isinstance(required, (list, tuple)):
                endpoint_ids.update(item for item in required if isinstance(item, str))
    for contract in store.contracts.values():
        if contract.kind != "api_contract":
            continue
        contract_id = contract.content.get("id")
        endpoints = contract.content.get("endpoints", ())
        endpoint_matches = {
            item.get("id")
            for item in endpoints
            if isinstance(item, Mapping) and item.get("id") in endpoint_ids
        } if isinstance(endpoints, (list, tuple)) else set()
        if contract_id in api_ids or endpoint_matches:
            if isinstance(contract_id, str):
                api_ids.add(contract_id)
            endpoint_ids.update(item for item in endpoint_matches if isinstance(item, str))
            entities = contract.content.get("entity_ids", ())
            if isinstance(entities, (list, tuple)):
                entity_ids.update(item for item in entities if isinstance(item, str))
    return page_ids, api_ids, endpoint_ids, entity_ids


def _binding_matches_targets(
    *,
    unit_id: str,
    unit_kind: BuildUnitKind,
    binding: FormalContractSourceRef,
    contract: FrozenContract,
    targets: tuple[set[str], set[str], set[str], set[str]],
) -> bool:
    """拒绝真实 Store ref 被错误授权给不相关的 Page、API、Entity 或权限目标。"""

    page_ids, api_ids, endpoint_ids, entity_ids = targets
    if binding.kind in {"product_plan", "technical_plan"}:
        return True
    if binding.kind == "page_contract":
        page_id = contract.content.get("pageId")
        return (
            unit_kind == "page"
            and isinstance(page_id, str)
            and unit_id == f"page:{page_id}"
            and page_id in page_ids
        )
    if binding.kind == "api_contract":
        return contract.content.get("id") in api_ids
    if binding.kind == "entity_binding":
        return contract.content.get("entity_id") in entity_ids
    if binding.kind == "authorization_slice":
        if contract.content.get("pageId") in page_ids:
            return True
        endpoints = contract.content.get("endpoints", ())
        return isinstance(endpoints, (list, tuple)) and any(
            isinstance(item, Mapping) and item.get("endpointId") in endpoint_ids
            for item in endpoints
        )
    return False


def build_unit_contract_catalog(
    *,
    unit_id: str,
    unit_kind: BuildUnitKind,
    generation_requirements: Sequence[Any],
    formal_source_refs: Sequence[FormalContractSourceRef | Mapping[str, Any]],
    frozen_contract_store: FrozenContractStore,
) -> tuple[ContractCatalogEntry, ...]:
    """按当前 Unit 和职责构建只指向 Frozen Store 的确定性 allowlist。

    这里只验证授权绑定和汇总 selector，不解释 selector、不读取合同 fragment，也不
    接受实时文件路径。其他 Unit 的绑定会被过滤，当前 Unit 的错误绑定则立即失败。
    """

    if not isinstance(unit_id, str) or not unit_id or unit_id != unit_id.strip():
        raise ContractCatalogBindingError("Unit contract catalog 缺少精确 unit_id。")
    store = FrozenContractStore.model_validate(frozen_contract_store)
    try:
        requirement_records = tuple(_requirement_record(item) for item in generation_requirements)
        bindings = tuple(FormalContractSourceRef.model_validate(item) for item in formal_source_refs)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ContractCatalogBindingError):
            raise
        raise ContractCatalogBindingError(f"正式来源绑定结构无效：{exc}") from exc
    requirement_ids = tuple(item[0] for item in requirement_records)
    if len(set(requirement_ids)) != len(requirement_ids):
        raise ContractCatalogBindingError("当前 Unit 存在重复 generation requirement。")

    expected_requirements = set(requirement_ids)
    targets = _authorized_targets(dict(requirement_records), store)
    covered_requirements: set[str] = set()
    selectors_by_ref: dict[str, set[str]] = {}
    kinds_by_ref: dict[str, ContractKind] = {}
    for binding in bindings:
        if binding.unit_id != unit_id:
            continue
        if binding.unit_kind != unit_kind:
            raise ContractCatalogBindingError(
                f"Unit {unit_id} 的正式来源绑定 unit_kind 不一致。"
            )
        unknown_requirements = set(binding.requirement_ids) - expected_requirements
        if unknown_requirements:
            raise ContractCatalogBindingError(
                f"Unit {unit_id} 的正式来源绑定引用了非当前职责："
                f"{', '.join(sorted(unknown_requirements))}。"
            )
        contract = store.get_by_source(kind=binding.kind, source=binding.source)
        if contract is None:
            raise ContractCatalogBindingError(
                f"Unit {unit_id} 的正式来源绑定不属于当前 FrozenContractStore：{binding.kind}。"
            )
        if not _binding_matches_targets(
            unit_id=unit_id,
            unit_kind=unit_kind,
            binding=binding,
            contract=contract,
            targets=targets,
        ):
            raise ContractCatalogBindingError(
                f"Unit {unit_id} 的正式来源绑定与当前职责目标不一致：{binding.kind}。"
            )
        covered_requirements.update(binding.requirement_ids)
        kinds_by_ref.setdefault(contract.ref_id, binding.kind)
        if kinds_by_ref[contract.ref_id] != binding.kind:
            raise ContractCatalogBindingError(
                f"Unit {unit_id} 的同一 ref_id 绑定了冲突的合同 kind。"
            )
        selectors_by_ref.setdefault(contract.ref_id, set()).update(binding.selectors)

    missing_requirements = expected_requirements - covered_requirements
    if missing_requirements:
        raise ContractCatalogBindingError(
            f"Unit {unit_id} 的 generation requirements 缺少正式来源绑定："
            f"{', '.join(sorted(missing_requirements))}。"
        )
    return tuple(
        ContractCatalogEntry(
            ref_id=ref_id,
            kind=kinds_by_ref[ref_id],
            selectors=tuple(sorted(selectors)),
        )
        for ref_id, selectors in sorted(selectors_by_ref.items())
    )
