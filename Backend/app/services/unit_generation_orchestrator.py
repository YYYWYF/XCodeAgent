"""串行执行单 Unit 的一轮 Local generation、validation 与 retry。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.global_planning_validation import (
    CandidateCompletenessResult,
    check_candidate_completeness,
)
from app.services.planning_issues import ValidationIssue
from app.services.planning_run_contracts import UnitRunState
from app.services.planning_run_controller import PlanningRunController
from app.services.planning_run_events import (
    CandidateInvalid,
    CandidateReady,
    GlobalCheckStarted,
    RoundExhausted,
    RunFailed,
    UnitAttemptStarted,
    UnitValidationStarted,
)
from app.services.unit_candidate_validator import validate_unit_candidate
from app.services.unit_generation import (
    UnitGenerationInfrastructureError,
    UnitGenerationPlatformError,
    generate_unit_candidate_once,
)
from app.services.unit_generation_contracts import (
    AttemptIdentity,
    CandidateAttempt,
    UnitAttemptJob,
    UnitGenerationAttemptResult,
    UnitGenerationContext,
    UnitGenerationPolicy,
)


_GenerateOnce = Callable[..., Awaitable[UnitGenerationAttemptResult]]
_ValidateCandidate = Callable[
    [
        UnitGenerationContext,
        Sequence[Mapping[str, Any]],
        ReuseFacts | Mapping[str, Any] | None,
    ],
    list[ValidationIssue],
]
_Clock = Callable[[], str]


class UnitGenerationFatalError(RuntimeError):
    """表示 Local Validator 返回了不能进入当前 Unit 内容重试的致命问题。"""

    def __init__(self, issues: Sequence[ValidationIssue]) -> None:
        """保存完整致命问题，便于上层诊断且不把问题改写成可重试内容错误。"""

        self.issues = tuple(
            ValidationIssue.model_validate(issue) for issue in issues
        )
        super().__init__("Unit generation stopped by a non-retryable local validation result.")


class GenerationRoundBarrierPending(RuntimeError):
    """表示仍有被调度 Unit 未到达当前 generation round 终态。"""

    def __init__(self, pending_unit_ids: Sequence[str]) -> None:
        """保存稳定排序的等待集合，供调度方继续处理而不是提前进入 Global。"""

        self.pending_unit_ids = tuple(sorted(dict.fromkeys(pending_unit_ids)))
        super().__init__(
            "Generation round is still waiting for Units: "
            + ", ".join(self.pending_unit_ids)
        )


def _utc_timestamp() -> str:
    """为状态事件生成 UTC 时间；测试可注入固定时钟保证结果稳定。"""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _local_retry_issues(
    issues: Sequence[ValidationIssue],
    *,
    unit_id: str,
) -> tuple[ValidationIssue, ...] | None:
    """只接纳完整归因到当前 Unit 的可重试 generation Issues。"""

    frozen = tuple(ValidationIssue.model_validate(issue) for issue in issues)
    if frozen and all(
        issue.level == "unit"
        and issue.category == "generation"
        and issue.retryable
        and set(issue.retry_unit_ids) == {unit_id}
        for issue in frozen
    ):
        return frozen
    return None


def _latest_local_feedback(unit: UnitRunState) -> tuple[ValidationIssue, ...]:
    """从当前 Unit 快照提取最近一次 Local 内容问题，不累计更早 Attempt。"""

    return tuple(
        issue
        for issue in unit.current_issues
        if issue.level == "unit"
        and issue.category == "generation"
        and issue.retryable
        and set(issue.retry_unit_ids) == {unit.unit_id}
    )


def _infrastructure_issue(error: UnitGenerationInfrastructureError) -> ValidationIssue:
    """把单次模型基础设施异常转换为可持久化的 PlanningRun fatal Issue。"""

    return ValidationIssue(
        code="UNIT_GENERATION_INFRASTRUCTURE_FAILURE",
        level="system",
        category="infrastructure",
        unit_ids=(error.identity.unit_id,),
        task_ids=(),
        retry_unit_ids=(),
        retryable=False,
        message="Unit Candidate 生成发生模型基础设施错误，PlanningRun 已终止。",
        details={
            "attempt_id": error.identity.attempt_id,
            "stage": error.stage,
            "cause_type": error.cause_type,
        },
    )


def _platform_issue(error: UnitGenerationPlatformError) -> ValidationIssue:
    """把冻结来源/Reader 损坏转换为不可进入 Local/Global 重试的平台问题。"""

    return ValidationIssue(
        code="UNIT_GENERATION_FROZEN_CONTRACT_SOURCE_INVALID",
        level="system",
        category="platform",
        unit_ids=(error.identity.unit_id,),
        task_ids=(),
        retry_unit_ids=(),
        retryable=False,
        message="Unit Candidate 生成使用的冻结合同来源已损坏，PlanningRun 已终止。",
        details={
            "attempt_id": error.identity.attempt_id,
            "stage": error.stage,
            "cause_type": error.cause_type,
        },
    )


def _assert_round_inputs(
    controller: PlanningRunController,
    context: UnitGenerationContext,
    policy: UnitGenerationPolicy,
) -> UnitRunState:
    """在分配 Attempt 前校验 Run、Unit、冻结输入和固定 Local 策略的一致性。"""

    snapshot = controller.snapshot
    if snapshot.status != "active" or snapshot.phase != "generating_units":
        raise ValueError("Unit Local round 只能在 active/generating_units PlanningRun 中执行。")
    if context.planning_run_id != snapshot.planning_run_id:
        raise ValueError("UnitGenerationContext 不属于当前 PlanningRun。")
    if context.unit_id not in snapshot.unit_states:
        raise ValueError("UnitGenerationContext 的 Unit 不属于当前 PlanningRun。")
    unit = snapshot.unit_states[context.unit_id]
    if unit.generation_strategy != "model" or unit.generation_status != "pending":
        raise ValueError("Local generation 只接收 pending 的 model Unit。")
    if context.input_fingerprint != snapshot.input_fingerprint:
        raise ValueError("UnitGenerationContext 的 input fingerprint 与 PlanningRun 不一致。")
    if context.base_confirmed_plan_digest != snapshot.base_confirmed_plan_digest:
        raise ValueError("UnitGenerationContext 的 confirmed baseline digest 与 PlanningRun 不一致。")
    if context.build_execution_scope != snapshot.build_execution_scope:
        raise ValueError("UnitGenerationContext 的 Build scope 与 PlanningRun 不一致。")
    if unit.attempt_in_round >= policy.local_max_attempts:
        raise ValueError("Unit 已耗尽 Local attempt，必须先结束本轮。")
    return unit


async def _fail_validation(
    controller: PlanningRunController,
    issues: Sequence[ValidationIssue],
    *,
    now: _Clock,
) -> None:
    """以首个非重试问题终止 Run，异常对象仍保留完整 Validator 结果。"""

    fatal = next((issue for issue in issues if not issue.retryable), None)
    if fatal is None:
        fatal = ValidationIssue(
            code="UNIT_LOCAL_RETRY_CONTRACT_INVALID",
            level="system",
            category="platform",
            unit_ids=tuple(
                dict.fromkeys(
                    unit_id for issue in issues for unit_id in issue.unit_ids
                )
            ),
            task_ids=tuple(
                dict.fromkeys(
                    task_id for issue in issues for task_id in issue.task_ids
                )
            ),
            retry_unit_ids=(),
            retryable=False,
            message="Local Validator 返回了不符合当前 Unit 重试契约的问题。",
        )
    await controller.apply(RunFailed(issue=fatal, at=now()))


def allocate_unit_attempt_job(
    controller: PlanningRunController,
    context: UnitGenerationContext,
    policy: UnitGenerationPolicy,
) -> UnitAttemptJob:
    """从 Controller 已提交快照为 pending model Unit 分配下一次 Attempt Job。"""

    frozen_context = UnitGenerationContext.model_validate(context)
    frozen_policy = UnitGenerationPolicy.model_validate(policy)
    unit = _assert_round_inputs(controller, frozen_context, frozen_policy)
    identity = AttemptIdentity.allocate(
        planning_run_id=controller.snapshot.planning_run_id,
        unit_id=unit.unit_id,
        generation_round=unit.generation_round,
        attempt_in_round=unit.attempt_in_round + 1,
    )
    return UnitAttemptJob(
        identity=identity,
        context=frozen_context,
        policy=frozen_policy,
    )


async def run_unit_generation_attempt(
    controller: PlanningRunController,
    job: UnitAttemptJob,
    *,
    global_feedback: Sequence[ValidationIssue] = (),
    local_feedback: Sequence[ValidationIssue] = (),
    reuse_facts: ReuseFacts | Mapping[str, Any] | None = None,
    unit_kind_rules: Sequence[str] = (),
    settings: Settings | None = None,
    generate_once: _GenerateOnce | None = None,
    validate_candidate: _ValidateCandidate | None = None,
    now: _Clock = _utc_timestamp,
) -> UnitRunState:
    """执行并提交一个已分配 Attempt，返回可重排队或已终止的 Unit 状态。"""

    frozen_job = UnitAttemptJob.model_validate(job)
    frozen_global_feedback = tuple(
        ValidationIssue.model_validate(issue) for issue in global_feedback
    )
    frozen_local_feedback = tuple(
        ValidationIssue.model_validate(issue) for issue in local_feedback
    )
    unit = _assert_round_inputs(controller, frozen_job.context, frozen_job.policy)
    expected = (
        controller.snapshot.planning_run_id,
        unit.unit_id,
        unit.generation_round,
        unit.attempt_in_round + 1,
    )
    actual = (
        frozen_job.identity.planning_run_id,
        frozen_job.identity.unit_id,
        frozen_job.identity.generation_round,
        frozen_job.identity.attempt_in_round,
    )
    if actual != expected:
        raise ValueError("UnitAttemptJob 不是当前 Unit 下一次可执行的 Attempt。")
    if frozen_local_feedback != _latest_local_feedback(unit):
        raise ValueError("UnitAttemptJob 的 Local feedback 不是当前 Unit 最新内容问题。")

    active_generate_once = generate_once or generate_unit_candidate_once
    active_validate_candidate = validate_candidate or validate_unit_candidate
    identity = frozen_job.identity
    await controller.apply(UnitAttemptStarted(identity=identity, at=now()))

    try:
        result = await active_generate_once(
            frozen_job,
            global_feedback=frozen_global_feedback,
            local_feedback=frozen_local_feedback,
            unit_kind_rules=unit_kind_rules,
            settings=settings,
        )
    except UnitGenerationInfrastructureError as exc:
        # 先提交 Run.failed；并发 Scheduler 随后负责停派、取消 sibling 并传播原始异常。
        await controller.apply(RunFailed(issue=_infrastructure_issue(exc), at=now()))
        raise
    except UnitGenerationPlatformError as exc:
        # Reader/Store 损坏不是模型内容错误，必须在本 Attempt 处终止整次 PlanningRun。
        await controller.apply(RunFailed(issue=_platform_issue(exc), at=now()))
        raise

    result = UnitGenerationAttemptResult.model_validate(result)
    if (
        result.identity != identity
        or result.input_fingerprint != frozen_job.context.input_fingerprint
    ):
        mismatch = ValidationIssue(
            code="UNIT_GENERATION_RESULT_IDENTITY_MISMATCH",
            level="system",
            category="platform",
            unit_ids=(unit.unit_id,),
            task_ids=(),
            retry_unit_ids=(),
            retryable=False,
            message="Unit generation Worker 返回了非当前预期 Attempt 的结果。",
            details={"expected_attempt_id": identity.attempt_id},
        )
        await controller.apply(RunFailed(issue=mismatch, at=now()))
        raise UnitGenerationFatalError((mismatch,))

    issues = tuple(result.validation_issues)
    if not issues:
        await controller.apply(UnitValidationStarted(identity=identity, at=now()))
        issues = tuple(
            active_validate_candidate(frozen_job.context, result.tasks, reuse_facts)
        )

    if issues:
        local_issues = _local_retry_issues(issues, unit_id=unit.unit_id)
        if local_issues is None:
            await _fail_validation(controller, issues, now=now)
            raise UnitGenerationFatalError(issues)
        candidate = CandidateAttempt(
            identity=identity,
            input_fingerprint=result.input_fingerprint,
            status="invalid",
            tasks=result.tasks,
            validation_issues=local_issues,
            generation_metadata=result.generation_metadata,
        )
        await controller.apply(CandidateInvalid(candidate=candidate, at=now()))
        current = controller.snapshot.unit_states[unit.unit_id]
        if current.attempt_in_round == frozen_job.policy.local_max_attempts:
            await controller.apply(RoundExhausted(unit_id=unit.unit_id, at=now()))
        return controller.snapshot.unit_states[unit.unit_id]

    candidate = CandidateAttempt(
        identity=identity,
        input_fingerprint=result.input_fingerprint,
        status="valid",
        tasks=result.tasks,
        validation_issues=(),
        generation_metadata=result.generation_metadata,
    )
    await controller.apply(CandidateReady(candidate=candidate, at=now()))
    return controller.snapshot.unit_states[unit.unit_id]


async def run_unit_generation_round(
    controller: PlanningRunController,
    context: UnitGenerationContext,
    policy: UnitGenerationPolicy,
    *,
    global_feedback: Sequence[ValidationIssue] = (),
    reuse_facts: ReuseFacts | Mapping[str, Any] | None = None,
    unit_kind_rules: Sequence[str] = (),
    settings: Settings | None = None,
    generate_once: _GenerateOnce | None = None,
    validate_candidate: _ValidateCandidate | None = None,
    now: _Clock = _utc_timestamp,
) -> UnitRunState:
    """以 concurrency=1 跑完当前 Unit generation round。

    每个循环只执行一次 ``generate_unit_candidate_once``；下一 Attempt 必须等当前结果
    完成解析、Local Validation 和单写者提交后才分配。成功立即返回 candidate_ready，
    三次内容失败返回 round_exhausted；基础设施或非 Local 内容问题立即终止 Run。
    """

    frozen_context = UnitGenerationContext.model_validate(context)
    frozen_policy = UnitGenerationPolicy.model_validate(policy)
    unit = _assert_round_inputs(controller, frozen_context, frozen_policy)

    while unit.attempt_in_round < frozen_policy.local_max_attempts:
        job = allocate_unit_attempt_job(controller, frozen_context, frozen_policy)
        unit = await run_unit_generation_attempt(
            controller,
            job,
            global_feedback=global_feedback,
            local_feedback=_latest_local_feedback(unit),
            reuse_facts=reuse_facts,
            unit_kind_rules=unit_kind_rules,
            settings=settings,
            generate_once=generate_once,
            validate_candidate=validate_candidate,
            now=now,
        )
        if unit.generation_status in {"candidate_ready", "round_exhausted"}:
            return unit

    # 输入校验和循环条件共同保证不可达；保留保护以防固定策略未来改变。
    raise RuntimeError("Unit Local round exited without a terminal Unit status.")


async def complete_generation_round(
    controller: PlanningRunController,
    *,
    now: _Clock = _utc_timestamp,
) -> CandidateCompletenessResult:
    """通过 generation round Barrier，并返回 Candidate 完整性结论。

    本接口不等待异步任务，也不启动 Assembly/Global Repair；只在当前 Run 的全部 planning
    Unit 已到达 candidate_ready 或 round_exhausted 后提交 GlobalCheckStarted。未到达时
    抛出带 pending Unit IDs 的异常，Controller phase 保持 generating_units。
    """

    snapshot = controller.snapshot
    if snapshot.status != "active" or snapshot.phase != "generating_units":
        raise ValueError("Generation round Barrier 只接受 active/generating_units Run。")
    pending = tuple(
        unit_id
        for unit_id in snapshot.planning_unit_ids
        if snapshot.unit_states[unit_id].generation_status
        not in {"candidate_ready", "round_exhausted"}
    )
    if pending:
        raise GenerationRoundBarrierPending(pending)

    result = check_candidate_completeness(snapshot.unit_states)
    await controller.apply(GlobalCheckStarted(at=now()))
    return result
