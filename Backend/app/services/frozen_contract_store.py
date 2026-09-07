"""PlanningRun 范围内正式合同的递归只读存储与稳定引用。"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, PlainSerializer, StringConstraints, model_validator

from app.services.planning_frozen import (
    FrozenJsonObject,
    FrozenPlanningModel,
    freeze_json,
    plain_json,
    tuple_input,
)


ContractKind = Literal[
    "product_plan",
    "technical_plan",
    "page_contract",
    "api_contract",
    "entity_binding",
    "authorization_slice",
]
_Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]


class FormalContractInput(FrozenPlanningModel):
    """保存一份正式合同正文及其调用方提供的结构化来源证据。"""

    content: FrozenJsonObject
    source: FrozenJsonObject

    @model_validator(mode="after")
    def validate_source(self) -> "FormalContractInput":
        """拒绝缺失正文或来源证据的合同，避免冻结出不可用条目。"""

        if not self.content:
            raise ValueError("正式合同必须提供非空 content。")
        if not self.source:
            raise ValueError("正式合同必须提供非空 structured source。")
        return self


_ContractInputs = Annotated[tuple[FormalContractInput, ...], BeforeValidator(tuple_input)]


class PlanningFormalInputs(FrozenPlanningModel):
    """显式列出创建 PlanningRun 时允许进入 Store 的全部正式输入类别。"""

    product_plan: FormalContractInput
    technical_plan: FormalContractInput
    page_contracts: _ContractInputs
    api_contracts: _ContractInputs
    entity_bindings: _ContractInputs
    authorization_slices: _ContractInputs


class FrozenContract(FrozenPlanningModel):
    """Store 中一条可按稳定引用寻址的冻结正式合同。"""

    ref_id: _Identifier
    kind: ContractKind
    content: FrozenJsonObject
    source: FrozenJsonObject


def _contract_mapping(value):
    """序列化合同索引时复制映射外壳，内部模型交由 Pydantic 导出。"""

    return dict(value)


_ContractIndex = Annotated[
    Mapping[_Identifier, FrozenContract],
    BeforeValidator(plain_json),
    AfterValidator(freeze_json),
    PlainSerializer(_contract_mapping, return_type=dict[str, FrozenContract]),
]


def _canonical_json(value: Mapping) -> str:
    """使用稳定 JSON 编码构造与字典顺序无关的引用输入。"""

    return json.dumps(
        plain_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _stable_ref_id(planning_run_id: str, kind: ContractKind, source: Mapping) -> str:
    """由 Run、合同类别和正式来源身份生成本次 Run 内稳定的引用。"""

    identity = _canonical_json({
        "planning_run_id": planning_run_id,
        "kind": kind,
        "source": source,
    })
    return f"frozen-contract-{sha256(identity.encode('utf-8')).hexdigest()}"


class FrozenContractStore(FrozenPlanningModel):
    """仅持有一个 PlanningRun 的正式合同，不读取磁盘或保存 Candidate。"""

    planning_run_id: _Identifier
    contracts: _ContractIndex

    @classmethod
    def create(
        cls,
        *,
        planning_run_id: str,
        formal_inputs: PlanningFormalInputs,
    ) -> "FrozenContractStore":
        """一次性复制正式输入，并建立来源身份稳定且不会被覆盖的合同索引。"""

        inputs = PlanningFormalInputs.model_validate(formal_inputs)
        grouped: tuple[tuple[ContractKind, tuple[FormalContractInput, ...]], ...] = (
            ("product_plan", (inputs.product_plan,)),
            ("technical_plan", (inputs.technical_plan,)),
            ("page_contract", inputs.page_contracts),
            ("api_contract", inputs.api_contracts),
            ("entity_binding", inputs.entity_bindings),
            ("authorization_slice", inputs.authorization_slices),
        )
        contracts: dict[str, FrozenContract] = {}
        for kind, entries in grouped:
            for entry in entries:
                ref_id = _stable_ref_id(planning_run_id, kind, entry.source)
                if ref_id in contracts:
                    raise ValueError(f"正式合同来源重复，无法建立唯一 ref_id：{kind}。")
                contracts[ref_id] = FrozenContract(
                    ref_id=ref_id,
                    kind=kind,
                    content=entry.content,
                    source=entry.source,
                )
        return cls(planning_run_id=planning_run_id, contracts=contracts)

    @model_validator(mode="after")
    def validate_index(self) -> "FrozenContractStore":
        """拒绝伪造引用、索引错配或缺少两份核心正式计划的 Store。"""

        for ref_id, contract in self.contracts.items():
            expected = _stable_ref_id(self.planning_run_id, contract.kind, contract.source)
            if ref_id != contract.ref_id or ref_id != expected:
                raise ValueError("FrozenContractStore 索引键和 ref_id 必须匹配稳定来源身份。")
        kinds = tuple(contract.kind for contract in self.contracts.values())
        if kinds.count("product_plan") != 1 or kinds.count("technical_plan") != 1:
            raise ValueError("FrozenContractStore 必须且只能包含一份 ProductPlan 和 TechnicalPlan。")
        return self

    def get(self, ref_id: str) -> FrozenContract | None:
        """按稳定引用返回冻结合同；未知引用不回退读取任何实时来源。"""

        return self.contracts.get(ref_id)

    def get_by_source(
        self,
        *,
        kind: ContractKind,
        source: Mapping,
    ) -> FrozenContract | None:
        """按当前 Run 的合同类别和结构化来源解析 Store 条目。"""

        ref_id = _stable_ref_id(self.planning_run_id, kind, source)
        contract = self.contracts.get(ref_id)
        if contract is None or contract.kind != kind or contract.source != source:
            return None
        return contract

    def __getitem__(self, ref_id: str) -> FrozenContract:
        """提供显式索引读取，并让未知引用保持标准 KeyError 语义。"""

        return self.contracts[ref_id]

    @property
    def ref_ids(self) -> tuple[str, ...]:
        """以确定性排序返回当前 Run 的全部稳定引用。"""

        return tuple(sorted(self.contracts))
