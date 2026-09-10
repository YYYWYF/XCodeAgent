"""按 Unit 正式来源绑定构建 Frozen Contract Catalog / Allowlist。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator, StringConstraints, model_validator

from app.domain.models import BuildUnitKind
from app.services.frozen_contract_store import (
    ContractKind,
    FrozenContractStore,
)
from app.services.planning_frozen import (
    FrozenJsonObject,
    FrozenPlanningModel,
    plain_json,
    tuple_input,
)


_Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
_Selector = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]


def _sorted_strings(value: tuple[str, ...]) -> tuple[str, ...]:
    """把已完成字符串校验的序列规范化为稳定字典序。"""

    return tuple(sorted(value))


_Identifiers = Annotated[
    tuple[_Identifier, ...],
    BeforeValidator(tuple_input),
    AfterValidator(_sorted_strings),
]
_Selectors = Annotated[
    tuple[_Selector, ...],
    BeforeValidator(tuple_input),
    AfterValidator(_sorted_strings),
]


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
        """拒绝空或重复声明；顺序已在字段边界统一规范化。"""

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


def _canonical_source(value: Mapping[str, Any]) -> str:
    """把结构化来源编码为与映射插入顺序无关的稳定字符串。"""

    return json.dumps(
        plain_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def formal_source_ref_sort_key(
    binding: FormalContractSourceRef,
) -> tuple[str, str, str, str, tuple[str, ...], tuple[str, ...]]:
    """返回 FormalContractSourceRef 的完整稳定排序键。"""

    return (
        binding.unit_id,
        binding.unit_kind,
        binding.kind,
        _canonical_source(binding.source),
        binding.requirement_ids,
        binding.selectors,
    )


def canonicalize_formal_source_refs(
    bindings: tuple[FormalContractSourceRef, ...],
) -> tuple[FormalContractSourceRef, ...]:
    """规范化顶层正式来源顺序，使语义等价输入产生相同序列化结果。"""

    return tuple(sorted(bindings, key=formal_source_ref_sort_key))


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


def _current_unit_bindings(
    *,
    unit_id: str,
    unit_kind: BuildUnitKind,
    expected_requirements: set[str],
    bindings: Sequence[FormalContractSourceRef],
    allow_other_units: bool,
) -> tuple[FormalContractSourceRef, ...]:
    """筛选并验证当前 Unit 的绑定身份，不让外部 Unit 绑定参与比较。"""

    result = []
    for binding in bindings:
        if binding.unit_id != unit_id:
            if allow_other_units:
                continue
            raise ContractCatalogBindingError("期望正式来源清单包含了其他 Unit 的绑定。")
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
        result.append(binding)
    return tuple(result)


def _binding_atoms(
    *,
    unit_id: str,
    bindings: Sequence[FormalContractSourceRef],
    store: FrozenContractStore,
) -> tuple[tuple[str, ContractKind, str, str], ...]:
    """把绑定展开为 requirement/kind/ref/selector 原子并验证均指向当前 Store。"""

    atoms = []
    for binding in bindings:
        contract = store.get_by_source(kind=binding.kind, source=binding.source)
        if contract is None:
            raise ContractCatalogBindingError(
                f"Unit {unit_id} 的正式来源绑定不属于当前 FrozenContractStore：{binding.kind}。"
            )
        atoms.extend(
            (requirement_id, binding.kind, contract.ref_id, selector)
            for requirement_id in binding.requirement_ids
            for selector in binding.selectors
        )
    if len(set(atoms)) != len(atoms):
        raise ContractCatalogBindingError(
            f"Unit {unit_id} 的正式来源绑定包含重复授权。"
        )
    return tuple(sorted(atoms))


def _binding_mismatch_summary(
    atoms: set[tuple[str, ContractKind, str, str]],
) -> str:
    """把原子差异压缩为稳定的 requirement/kind 列表，避免暴露正文。"""

    return ", ".join(sorted({f"{item[0]}/{item[1]}" for item in atoms}))


def build_unit_contract_catalog(
    *,
    unit_id: str,
    unit_kind: BuildUnitKind,
    generation_requirements: Sequence[Any],
    formal_source_refs: Sequence[FormalContractSourceRef | Mapping[str, Any]],
    expected_formal_source_refs: Sequence[FormalContractSourceRef | Mapping[str, Any]],
    frozen_contract_store: FrozenContractStore,
) -> tuple[ContractCatalogEntry, ...]:
    """按当前 Unit 和职责构建只指向 Frozen Store 的确定性 allowlist。

    这里只验证授权绑定和汇总 selector，不解释 selector、不读取合同 fragment，也不
    接受实时文件路径。其他 Unit 的绑定会被过滤，当前 Unit 的错误绑定则立即失败。
    """

    if not isinstance(unit_id, str) or not unit_id or unit_id != unit_id.strip():
        raise ContractCatalogBindingError("Unit contract catalog 缺少精确 unit_id。")
    if not isinstance(frozen_contract_store, FrozenContractStore):
        raise ContractCatalogBindingError(
            "Unit contract catalog 只接受已验证的 FrozenContractStore instance。"
        )
    store = frozen_contract_store
    try:
        requirement_records = tuple(_requirement_record(item) for item in generation_requirements)
        bindings = tuple(FormalContractSourceRef.model_validate(item) for item in formal_source_refs)
        expected_bindings = tuple(
            FormalContractSourceRef.model_validate(item)
            for item in expected_formal_source_refs
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ContractCatalogBindingError):
            raise
        raise ContractCatalogBindingError(f"正式来源绑定结构无效：{exc}") from exc
    requirement_ids = tuple(item[0] for item in requirement_records)
    if len(set(requirement_ids)) != len(requirement_ids):
        raise ContractCatalogBindingError("当前 Unit 存在重复 generation requirement。")

    expected_requirements = set(requirement_ids)
    current_bindings = _current_unit_bindings(
        unit_id=unit_id,
        unit_kind=unit_kind,
        expected_requirements=expected_requirements,
        bindings=bindings,
        allow_other_units=True,
    )
    expected_current_bindings = _current_unit_bindings(
        unit_id=unit_id,
        unit_kind=unit_kind,
        expected_requirements=expected_requirements,
        bindings=expected_bindings,
        allow_other_units=False,
    )
    manifest_requirements = {
        requirement_id
        for binding in expected_current_bindings
        for requirement_id in binding.requirement_ids
    }
    if manifest_requirements != expected_requirements:
        raise ContractCatalogBindingError(
            f"Unit {unit_id} 的确定性期望清单未精确覆盖全部 generation requirements。"
        )
    actual_atoms = set(_binding_atoms(unit_id=unit_id, bindings=current_bindings, store=store))
    expected_atoms = set(
        _binding_atoms(unit_id=unit_id, bindings=expected_current_bindings, store=store)
    )
    missing_atoms = expected_atoms - actual_atoms
    unexpected_atoms = actual_atoms - expected_atoms
    if missing_atoms or unexpected_atoms:
        details = []
        if missing_atoms:
            details.append(f"缺少 {_binding_mismatch_summary(missing_atoms)}")
        if unexpected_atoms:
            details.append(f"未授权 {_binding_mismatch_summary(unexpected_atoms)}")
        raise ContractCatalogBindingError(
            f"Unit {unit_id} 的正式来源绑定与确定性期望清单不一致：{'；'.join(details)}。"
        )

    selectors_by_ref: dict[str, set[str]] = {}
    kinds_by_ref: dict[str, ContractKind] = {}
    for binding in current_bindings:
        contract = store.get_by_source(kind=binding.kind, source=binding.source)
        if contract is None:  # pragma: no cover - 已由 _binding_atoms fail closed。
            raise ContractCatalogBindingError("已验证的正式来源绑定无法再次解析。")
        kinds_by_ref.setdefault(contract.ref_id, binding.kind)
        if kinds_by_ref[contract.ref_id] != binding.kind:
            raise ContractCatalogBindingError(
                f"Unit {unit_id} 的同一 ref_id 绑定了冲突的合同 kind。"
            )
        selectors_by_ref.setdefault(contract.ref_id, set()).update(binding.selectors)

    return tuple(
        ContractCatalogEntry(
            ref_id=ref_id,
            kind=kinds_by_ref[ref_id],
            selectors=tuple(sorted(selectors)),
        )
        for ref_id, selectors in sorted(selectors_by_ref.items())
    )
