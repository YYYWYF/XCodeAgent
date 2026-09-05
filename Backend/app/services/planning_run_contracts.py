"""PlanningRun 的纯内存冻结快照；不读时钟、不分配身份、不持久化。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, Field, PlainSerializer, StringConstraints, model_validator

from app.domain.models import BuildUnitKind
from app.services.planning_frozen import FrozenJsonObject, FrozenPlanningModel, freeze_json, plain_json, tuple_input
from app.services.planning_issues import ValidationIssue
from app.services.unit_generation_contracts import AttemptIdentity, CandidateAttempt
from app.services.unit_generation_requirements_contracts import GenerationStrategy


Id = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
Ids = Annotated[tuple[Id, ...], BeforeValidator(tuple_input)]
Issues = Annotated[tuple[ValidationIssue, ...], BeforeValidator(tuple_input)]
Participation = Literal["reuse_only", "generate_only", "reuse_and_generate", "prerequisite_only", "structural_only"]
GenerationStatus = Literal[
    "not_required", "pending", "generating", "validating", "candidate_ready", "round_exhausted", "aborted",
]
RunPhase = Literal["preparing", "generating_units", "global_check", "assembling", "validating", "persisting_pending"]
GENERATING_PARTICIPATIONS = frozenset({"generate_only", "reuse_and_generate"})


class UnitRoundHistory(FrozenPlanningModel):
    """保存被 Global reopen 关闭的一轮事实，旧候选状态以 Run 候选表为准。"""

    generation_round: Annotated[int, Field(ge=1)]
    attempt_in_round: Annotated[int, Field(ge=0, le=3)]
    generation_status: Literal["candidate_ready", "round_exhausted"]
    candidate_id: Id | None
    issues: Issues


class UnitRunState(FrozenPlanningModel):
    """Unit 生命周期快照；attempt 计数只表示模型预算，deterministic 保持零。"""

    unit_id: Id
    kind: BuildUnitKind
    participation: Participation
    generation_strategy: GenerationStrategy
    generation_status: GenerationStatus = "pending"
    generation_round: Annotated[int, Field(ge=1, le=3)] = 1
    attempt_in_round: Annotated[int, Field(ge=0, le=3)] = 0
    total_attempts: Annotated[int, Field(ge=0, le=9)] = 0
    retained_task_ids: Ids = ()
    reusable_capabilities: Ids = ()
    latest_candidate_id: Id | None = None
    candidate_task_count: Annotated[int, Field(ge=0)] = 0
    current_issues: Issues = ()
    round_history: Annotated[tuple[UnitRoundHistory, ...], BeforeValidator(tuple_input)] = ()
    expected_identity: AttemptIdentity | None = None

    @classmethod
    def create(
        cls, *, unit_id: str, kind: BuildUnitKind, generation_strategy: GenerationStrategy,
        retained_task_ids: tuple[str, ...] = (), reusable_capabilities: tuple[str, ...] = (),
    ) -> UnitRunState:
        """从已确定的生成策略创建初态，不重新计算 ReuseFacts 或职责缺项。"""

        generates = generation_strategy in {"model", "deterministic"}
        participation = ("reuse_and_generate" if retained_task_ids else "generate_only") if generates else generation_strategy
        return cls(
            unit_id=unit_id, kind=kind, generation_strategy=generation_strategy,
            participation=participation, generation_status="pending" if generates else "not_required",
            retained_task_ids=retained_task_ids, reusable_capabilities=reusable_capabilities,
        )

    @model_validator(mode="after")
    def validate_lifecycle(self) -> UnitRunState:
        """校验参与方式、模型预算及候选/在途身份与状态的一致性。"""

        fixed = {"frontend:shell": "prerequisite_only", "application:root": "structural_only", "app:integration": "structural_only"}
        if self.unit_id in fixed and (self.participation != fixed[self.unit_id] or self.generation_strategy != fixed[self.unit_id]):
            raise ValueError("shell/structural Unit 的参与方式和生成策略不可改变。")
        if self.unit_id == "frontend:auth-guard" and self.generation_strategy not in {"deterministic", "reuse_only"}:
            raise ValueError("auth-guard 只能复用或产生 deterministic Candidate。")
        generates = self.participation in GENERATING_PARTICIPATIONS
        if generates != (self.generation_strategy in {"model", "deterministic"}):
            raise ValueError("只有生成参与者可以使用 model/deterministic 策略。")
        if not generates and (self.participation != self.generation_strategy or self.generation_status != "not_required"):
            raise ValueError("非生成 Unit 必须保持 not_required 及匹配策略。")
        if generates and self.generation_status == "not_required":
            raise ValueError("生成 Unit 不能跳过本轮 Candidate。")
        if self.generation_strategy != "model" and (self.attempt_in_round or self.total_attempts):
            raise ValueError("非模型 Unit 不消耗模型 attempt budget。")
        if self.total_attempts != sum(item.attempt_in_round for item in self.round_history) + self.attempt_in_round:
            raise ValueError("累计模型次数必须等于历史轮次加当前轮次。")
        if tuple(item.generation_round for item in self.round_history) != tuple(range(1, self.generation_round)):
            raise ValueError("round_history 必须连续覆盖已关闭的 generation rounds。")
        if (self.generation_status in {"generating", "validating"}) != (self.expected_identity is not None):
            raise ValueError("只有在途生成/校验状态必须保存 expected_identity。")
        if self.expected_identity is not None:
            expected_attempt = self.attempt_in_round if self.generation_strategy == "model" else 1
            if (self.expected_identity.unit_id, self.expected_identity.generation_round, self.expected_identity.attempt_in_round) != (
                self.unit_id, self.generation_round, expected_attempt,
            ):
                raise ValueError("在途身份必须绑定当前 Unit/round/attempt。")
        ready = self.generation_status == "candidate_ready"
        if ready != (self.latest_candidate_id is not None) or ready != (self.candidate_task_count > 0):
            raise ValueError("仅 candidate_ready 可以且必须引用非空当前 Candidate。")
        if self.generation_status == "round_exhausted" and (self.generation_strategy != "model" or self.attempt_in_round != 3):
            raise ValueError("round_exhausted 只表示模型本轮三次预算耗尽。")
        return self


def _mapping_copy(value: Mapping) -> dict:
    """序列化时复制映射外壳，嵌套领域模型交给 Pydantic 导出。"""

    return dict(value)


_UnitStates = Annotated[
    Mapping[Id, UnitRunState], BeforeValidator(plain_json), AfterValidator(freeze_json),
    PlainSerializer(_mapping_copy, return_type=dict[str, UnitRunState]),
]
_Candidates = Annotated[
    Mapping[Id, CandidateAttempt], BeforeValidator(plain_json), AfterValidator(freeze_json),
    PlainSerializer(_mapping_copy, return_type=dict[str, CandidateAttempt]),
]


class PlanningRun(FrozenPlanningModel):
    """单写者持有的完整内存状态；纯转换返回新快照，旧快照不可作为新权威恢复。"""

    planning_run_id: Id
    workflow_run_id: Id
    thread_id: Id
    revision: Annotated[int, Field(ge=0)] = 0
    status: Literal["active", "failed", "cancelled"] = "active"
    phase: RunPhase = "preparing"
    build_execution_scope: FrozenJsonObject
    input_fingerprint: Id
    base_confirmed_plan_digest: Id | None
    required_unit_ids: Ids
    planning_unit_ids: Ids
    global_repair_round: Annotated[int, Field(ge=0, le=2)] = 0
    global_repair_limit: Annotated[int, Field(ge=2, le=2)] = 2
    global_issues: Issues = ()
    unit_states: _UnitStates
    candidates: _Candidates = Field(default_factory=dict, validate_default=True)
    started_at: Id
    updated_at: Id
    failure: ValidationIssue | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> PlanningRun:
        """拒绝范围错配、悬空 Candidate、过期有效 Candidate 及终态在途任务。"""

        required = set(self.required_unit_ids)
        planning = {key for key, unit in self.unit_states.items() if unit.participation in GENERATING_PARTICIPATIONS}
        if len(required) != len(self.required_unit_ids) or required != set(self.unit_states):
            raise ValueError("unit_states 必须精确覆盖唯一的 required_unit_ids。")
        if len(set(self.planning_unit_ids)) != len(self.planning_unit_ids) or set(self.planning_unit_ids) != planning:
            raise ValueError("planning_unit_ids 必须精确覆盖生成参与者。")
        if (self.status == "failed") != (self.failure is not None):
            raise ValueError("failed 必须且只能携带 failure。")
        attempt_ids = []
        current_ids = set()
        for key, unit in self.unit_states.items():
            if key != unit.unit_id or unit.generation_round > self.global_repair_round + 1:
                raise ValueError("Unit key/轮次必须属于当前 Run。")
            if self.status != "active" and unit.generation_status in {"pending", "generating", "validating"}:
                raise ValueError("终态 Run 不得保留可执行或在途 Unit。")
            if unit.expected_identity:
                if unit.expected_identity.planning_run_id != self.planning_run_id:
                    raise ValueError("在途身份不能属于其他 Run。")
                attempt_ids.append(unit.expected_identity.attempt_id)
            if unit.latest_candidate_id:
                candidate = self.candidates.get(unit.latest_candidate_id)
                if candidate is None or candidate.status != "valid" or candidate.validation_issues or not candidate.tasks:
                    raise ValueError("当前 Candidate 必须存在、valid、非空且无 Issue。")
                if (candidate.identity.unit_id, candidate.identity.generation_round, len(candidate.tasks)) != (
                    key, unit.generation_round, unit.candidate_task_count,
                ):
                    raise ValueError("当前 Candidate 必须匹配 Unit、轮次和任务数。")
                current_ids.add(candidate.candidate_id)
        for key, candidate in self.candidates.items():
            if key != candidate.candidate_id or candidate.identity.planning_run_id != self.planning_run_id or candidate.identity.unit_id not in planning:
                raise ValueError("Candidate 身份必须属于当前 Run 的生成 Unit。")
            if candidate.input_fingerprint != self.input_fingerprint:
                raise ValueError("Candidate 必须绑定冻结输入指纹。")
            if candidate.status == "valid" and key not in current_ids:
                raise ValueError("旧有效 Candidate 必须 supersede，不能作为隐藏回退。")
            attempt_ids.append(candidate.identity.attempt_id)
        if len(set(attempt_ids)) != len(attempt_ids):
            raise ValueError("每次 Attempt 身份只能被分配或记录一次。")
        return self
