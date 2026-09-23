"""Unit generation 领域 DTO；只定义数据边界，不构建上下文或执行生成、校验及重试。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import AfterValidator, BeforeValidator, Field, PlainSerializer, StringConstraints, model_validator

from app.domain.models import BuildUnitKind
from app.services.frozen_contract_catalog import ContractCatalogEntry
from app.services.frozen_contract_reader_contracts import FrozenContractReadPolicy
from app.services.planning_issues import ValidationIssue
from app.services.planning_frozen import (
    FrozenJsonObject as _FrozenJsonObject, FrozenPlanningModel as _GenerationModel,
    freeze_json as _freeze_json, plain_json as _plain_json, tuple_input as _tuple_input,
)


_Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
_PositiveInt = Annotated[int, Field(gt=0)]
_PositiveSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
_ReadLimits = Annotated[
    Mapping[_Identifier, _PositiveInt], BeforeValidator(_plain_json), AfterValidator(_freeze_json),
    PlainSerializer(_plain_json, return_type=dict[str, int]),
]
_ContractCatalog = Annotated[tuple[ContractCatalogEntry, ...], BeforeValidator(_tuple_input)]


class GenerationRequirement(_GenerationModel):
    """描述本 Unit 本轮需要新增的职责；不计算 ReuseFacts 或缺项。

    当前最小形状为显式 requirement_id、description 和只读 source_refs。
    source_refs 保存规则层提供的来源信息，不读取或推断正式合同。
    """

    requirement_id: _Identifier
    description: _Identifier
    source_refs: _FrozenJsonObject = Field(default_factory=dict, validate_default=True)


class UnitGenerationContext(_GenerationModel):
    """冻结的业务输入；所有业务区段必须显式提供，空 confirmed 基线用 None 表示。

    contract_catalog 只保存当前 Unit 的 Frozen Store 引用和 selector allowlist，其他区段
    保存工作区、依赖和约束快照。合同正文不进入 Context；本模型也不读取 fragment。
    retry counter、timeout 和 token budget 均不是本模型字段。
    """

    planning_run_id: _Identifier
    unit_id: _Identifier
    unit_kind: BuildUnitKind
    build_execution_scope: _FrozenJsonObject
    input_fingerprint: _Identifier
    base_confirmed_plan_digest: _Identifier | None
    generation_requirements: Annotated[tuple[GenerationRequirement, ...], BeforeValidator(_tuple_input)]
    contract_catalog: _ContractCatalog
    workspace_context: _FrozenJsonObject
    dependency_context: _FrozenJsonObject
    constraints: _FrozenJsonObject

    @model_validator(mode="after")
    def validate_contract_catalog(self) -> "UnitGenerationContext":
        """拒绝重复或顺序不稳定的 catalog 引用，保证 retry 可精确比较。"""

        ref_ids = tuple(entry.ref_id for entry in self.contract_catalog)
        if len(set(ref_ids)) != len(ref_ids) or tuple(sorted(ref_ids)) != ref_ids:
            raise ValueError("contract_catalog ref_id 必须唯一并按字典序排列。")
        return self


class UnitGenerationPolicy(_GenerationModel):
    """独立运行策略，时间单位为秒；保护参数由调用方显式提供，不读取 Settings。

    本 DTO 的 Local=3、SDK max_retries 默认=0（允许显式配置 0-2）、token budget=4096
    遵循设计基线；production_unit_generation_policy() 会另外显式配置 SDK max_retries=2。
    session timeout、turn limit、read limits 由 production policy 显式提供。
    read limits 仅容纳具名正整数预算，不承载合同正文。
    """

    local_max_attempts: Annotated[int, Field(ge=3, le=3)] = 3
    model_max_retries: Annotated[int, Field(ge=0, le=2)] = 0
    model_max_tokens: _PositiveInt = 4096
    request_timeout: _PositiveSeconds
    unit_session_timeout: _PositiveSeconds
    model_turn_limit: _PositiveInt
    frozen_contract_read_limits: _ReadLimits

    @model_validator(mode="after")
    def validate_frozen_contract_read_limits(self) -> "UnitGenerationPolicy":
        """要求 Unit Policy 完整承载 Reader 的三项保护预算且不接受任意扩展键。"""

        required = {"max_reads", "max_total_bytes", "max_bytes_per_read"}
        actual = set(self.frozen_contract_read_limits)
        if actual != required:
            raise ValueError(
                "frozen_contract_read_limits 必须且只能包含 "
                "max_reads、max_total_bytes、max_bytes_per_read。"
            )
        FrozenContractReadPolicy.model_validate(
            _plain_json(self.frozen_contract_read_limits)
        )
        return self


def _new_attempt_id() -> str:
    """在平台 dispatch 前分配 Attempt 身份，不使用 Candidate 或模型 Task ID。"""

    return f"attempt-{uuid4().hex}"


class AttemptIdentity(_GenerationModel):
    """一次 dispatch 的平台身份；结果必须原样回传，不能在响应到达时重新分配。"""

    planning_run_id: _Identifier
    unit_id: _Identifier
    generation_round: _PositiveInt
    attempt_in_round: _PositiveInt
    attempt_id: Annotated[str, StringConstraints(pattern=r"^attempt-[0-9a-f]{32}$")]

    @classmethod
    def allocate(
        cls, *, planning_run_id: str, unit_id: str, generation_round: int, attempt_in_round: int,
    ) -> "AttemptIdentity":
        """仅供平台在 dispatch 前分配身份；反序列化必须提供已有 attempt_id。"""

        return cls(
            planning_run_id=planning_run_id, unit_id=unit_id,
            generation_round=generation_round, attempt_in_round=attempt_in_round,
            attempt_id=_new_attempt_id(),
        )


class CandidateIdentity(_GenerationModel):
    """Candidate 当前归属身份；不包含任何当前 Run 的 Attempt 计数或 ID。"""

    planning_run_id: _Identifier
    unit_id: _Identifier
    generation_round: _PositiveInt

    @classmethod
    def from_attempt(cls, attempt: AttemptIdentity) -> "CandidateIdentity":
        """从已分配 Attempt 确定性派生 Candidate 当前身份，避免双重传入不一致。"""

        attempt = AttemptIdentity.model_validate(attempt)
        return cls(
            planning_run_id=attempt.planning_run_id,
            unit_id=attempt.unit_id,
            generation_round=attempt.generation_round,
        )


class CandidateRecoverySource(_GenerationModel):
    """记录 recovered Candidate 的来源引用，不承载 Workflow 或会话所有权。"""

    source_planning_run_id: _Identifier
    source_candidate_id: _Identifier


class UnitAttemptJob(_GenerationModel):
    """Worker 输入的冻结封装；仅校验身份一致性，不执行调度或过期结果判断。"""

    identity: AttemptIdentity
    context: UnitGenerationContext
    policy: UnitGenerationPolicy

    @model_validator(mode="after")
    def validate_context_identity(self) -> "UnitAttemptJob":
        """拒绝将其他 Run 或 Unit 的 Context 错投给当前 Attempt。"""

        if (self.identity.planning_run_id, self.identity.unit_id) != (
            self.context.planning_run_id, self.context.unit_id,
        ):
            raise ValueError("Attempt identity 与 Context 的 planning_run_id/unit_id 必须一致。")
        return self


def _new_candidate_id() -> str:
    """在平台创建 Candidate 时分配独立身份，绝不从模型 Task ID 推导。"""

    return f"candidate-{uuid4().hex}"


class CandidateAttempt(_GenerationModel):
    """平台封装的候选记录，拆分当前身份与产生来源，status 由调用方明确指定。

    candidate_id 默认由平台生成；反序列化可恢复原 ID。from_generated_attempt() 仅在
    candidate_id 为 None 时分配新 ID，显式值必须通过自身字段校验。后续模型响应适配器
    只能提交 tasks，不得把模型输出直接展开为本 DTO 的平台元数据。
    tasks 保留原始任务正文，包括非法或缺失 Task ID，供后续 Validator 报错。
    validation_issues 复用 T1.1 契约，不在此实现归因或状态转换。Candidate 当前
    identity 只表达当前 Run/Unit/round；generated_from 或 recovered_from 才表达来源。
    """

    candidate_id: Annotated[str, StringConstraints(pattern=r"^candidate-[0-9a-f]{32}$")] = Field(default_factory=_new_candidate_id)
    identity: CandidateIdentity
    origin: Literal["generated", "recovered"]
    generated_from: AttemptIdentity | None
    recovered_from: CandidateRecoverySource | None
    input_fingerprint: _Identifier
    status: Literal["valid", "invalid", "superseded"]
    tasks: Annotated[tuple[_FrozenJsonObject, ...], BeforeValidator(_tuple_input)]
    validation_issues: Annotated[tuple[ValidationIssue, ...], BeforeValidator(_tuple_input)] = ()
    generation_metadata: _FrozenJsonObject = Field(default_factory=dict, validate_default=True)

    @model_validator(mode="after")
    def validate_origin_provenance(self) -> "CandidateAttempt":
        """强制 generated/recovered 互斥，并拒绝伪造当前 Run Attempt 或自引用来源。"""

        if self.origin == "generated":
            if self.generated_from is None or self.recovered_from is not None:
                raise ValueError("generated Candidate 必须且只能携带 generated_from。")
            if CandidateIdentity.from_attempt(self.generated_from) != self.identity:
                raise ValueError("generated_from 必须与 Candidate 当前身份的 Run/Unit/round 一致。")
        else:
            if self.generated_from is not None or self.recovered_from is None:
                raise ValueError("recovered Candidate 必须且只能携带 recovered_from。")
            if self.recovered_from.source_planning_run_id == self.identity.planning_run_id:
                raise ValueError("recovered Candidate 的 source PlanningRun 不能等于当前 Run。")
            if self.recovered_from.source_candidate_id == self.candidate_id:
                raise ValueError("recovered Candidate 不能引用自身 Candidate ID。")
        return self

    @classmethod
    def from_generated_attempt(
        cls,
        *,
        attempt: AttemptIdentity,
        input_fingerprint: str,
        status: Literal["valid", "invalid", "superseded"],
        tasks: Sequence[Mapping[str, Any]],
        validation_issues: Sequence[ValidationIssue] = (),
        generation_metadata: Mapping[str, Any] | None = None,
        candidate_id: str | None = None,
    ) -> "CandidateAttempt":
        """用一次真实 Attempt 创建 generated Candidate；仅对 None candidate_id 自动分配。"""

        attempt = AttemptIdentity.model_validate(attempt)
        return cls(
            candidate_id=_new_candidate_id() if candidate_id is None else candidate_id,
            identity=CandidateIdentity.from_attempt(attempt),
            origin="generated",
            generated_from=attempt,
            recovered_from=None,
            input_fingerprint=input_fingerprint,
            status=status,
            tasks=tasks,
            validation_issues=validation_issues,
            generation_metadata={} if generation_metadata is None else generation_metadata,
        )

    @classmethod
    def from_recovered_candidate(
        cls,
        *,
        source_candidate: "CandidateAttempt",
        planning_run_id: str,
        unit_id: str,
        generation_round: int,
        input_fingerprint: str,
        candidate_id: str | None = None,
    ) -> "CandidateAttempt":
        """用当前 Run 的新身份接纳已重新校验的 source Candidate，不伪造当前 Attempt。"""

        source = cls.model_validate(source_candidate)
        return cls(
            candidate_id=_new_candidate_id() if candidate_id is None else candidate_id,
            identity=CandidateIdentity(
                planning_run_id=planning_run_id,
                unit_id=unit_id,
                generation_round=generation_round,
            ),
            origin="recovered",
            generated_from=None,
            recovered_from=CandidateRecoverySource(
                source_planning_run_id=source.identity.planning_run_id,
                source_candidate_id=source.candidate_id,
            ),
            input_fingerprint=input_fingerprint,
            status="valid",
            tasks=source.tasks,
            validation_issues=(),
            generation_metadata=source.generation_metadata,
        )


class UnitGenerationAttemptResult(_GenerationModel):
    """单次生成的未判定结果，不携带 Candidate status，也不自动生成 Candidate ID。

    保留原始响应和解析出的任务、结构化问题及调用元数据；是否成为 valid Candidate
    由后续平台 Validator 决定。这里只定义 DTO，不实现解析器或 LLM 调用。
    """

    identity: AttemptIdentity
    input_fingerprint: _Identifier
    raw_response: str
    tasks: Annotated[tuple[_FrozenJsonObject, ...], BeforeValidator(_tuple_input)]
    validation_issues: Annotated[tuple[ValidationIssue, ...], BeforeValidator(_tuple_input)] = ()
    generation_metadata: _FrozenJsonObject = Field(default_factory=dict, validate_default=True)
