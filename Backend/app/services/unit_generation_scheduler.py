"""以有限并发 FIFO 队列执行一个 Unit generation round。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import Field, model_validator

from app.config import Settings
from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.global_planning_validation import CandidateCompletenessResult
from app.services.planning_frozen import FrozenPlanningModel
from app.services.planning_issues import ValidationIssue
from app.services.planning_run_contracts import UnitRunState
from app.services.planning_run_controller import PlanningRunController
from app.services.unit_generation import (
    UnitGenerationInfrastructureError,
    UnitGenerationPlatformError,
)
from app.services.unit_generation_contracts import (
    UnitAttemptJob,
    UnitGenerationAttemptResult,
    UnitGenerationContext,
    UnitGenerationPolicy,
)
from app.services.unit_generation_orchestrator import (
    UnitGenerationFatalError,
    allocate_unit_attempt_job,
    complete_generation_round,
    run_unit_generation_attempt,
)


MAX_MODEL_CONCURRENCY = 3
_TERMINAL_STATUSES = frozenset({"candidate_ready", "round_exhausted"})
_GenerateOnce = Callable[..., Awaitable[UnitGenerationAttemptResult]]
_ValidateCandidate = Callable[
    [
        UnitGenerationContext,
        Sequence[Mapping[str, Any]],
        ReuseFacts | Mapping[str, Any] | None,
    ],
    list[ValidationIssue],
]
_DeterministicRunner = Callable[[UnitRunState], Awaitable[None]]
_Clock = Callable[[], str]


def _utc_timestamp() -> str:
    """为 Scheduler 的 Controller 事件生成 UTC 时间。"""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _latest_local_feedback(unit: UnitRunState) -> tuple[ValidationIssue, ...]:
    """只提取当前 Unit 最近一次可重试 Local generation 问题。"""

    return tuple(
        issue
        for issue in unit.current_issues
        if issue.level == "unit"
        and issue.category == "generation"
        and issue.retryable
        and set(issue.retry_unit_ids) == {unit.unit_id}
    )


class UnitGenerationRoundResult(FrozenPlanningModel):
    """记录 Scheduler 已完成的本轮参与者和 Barrier 齐全性结论。"""

    planning_run_id: str
    model_unit_ids: tuple[str, ...]
    deterministic_unit_ids: tuple[str, ...]
    candidate_ready_unit_ids: tuple[str, ...]
    round_exhausted_unit_ids: tuple[str, ...]
    model_attempt_count: int = Field(ge=0)
    completeness: CandidateCompletenessResult

    @model_validator(mode="after")
    def validate_terminal_partition(self) -> "UnitGenerationRoundResult":
        """保证本轮调度 Unit 唯一，并被 ready/exhausted 终态完整分区。"""

        scheduled = self.model_unit_ids + self.deterministic_unit_ids
        terminal = self.candidate_ready_unit_ids + self.round_exhausted_unit_ids
        if len(set(scheduled)) != len(scheduled):
            raise ValueError("UnitGenerationRoundResult 的调度 Unit 必须唯一。")
        if len(set(terminal)) != len(terminal) or set(terminal) != set(scheduled):
            raise ValueError("本轮调度 Unit 必须全部且仅能进入 ready/exhausted 终态。")
        if set(self.round_exhausted_unit_ids) - set(self.model_unit_ids):
            raise ValueError("只有 model Unit 可以进入 round_exhausted。")
        return self


@dataclass(frozen=True, slots=True)
class _QueuedAttempt:
    """冻结一个 FIFO 队列项，反馈与 Attempt 身份在入队时共同确定。"""

    job: UnitAttemptJob
    global_feedback: tuple[ValidationIssue, ...]
    local_feedback: tuple[ValidationIssue, ...]


class UnitGenerationScheduler:
    """为一个 PlanningRun 提供最多三个 model worker 的本轮调度。"""

    def __init__(self, *, concurrency: int = MAX_MODEL_CONCURRENCY) -> None:
        """校验配置并将实际模型并发硬限制在设计上限三。"""

        if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
            raise ValueError("Unit generation concurrency 必须是正整数。")
        self._concurrency = min(concurrency, MAX_MODEL_CONCURRENCY)

    @property
    def concurrency(self) -> int:
        """返回应用设计上限后的实际 model worker 数。"""

        return self._concurrency

    async def run_round(
        self,
        controller: PlanningRunController,
        contexts: Mapping[str, UnitGenerationContext],
        policy: UnitGenerationPolicy,
        *,
        global_feedback_by_unit: Mapping[str, Sequence[ValidationIssue]] | None = None,
        reuse_facts: ReuseFacts | Mapping[str, Any] | None = None,
        unit_kind_rules: Sequence[str] = (),
        settings: Settings | None = None,
        generate_once: _GenerateOnce | None = None,
        validate_candidate: _ValidateCandidate | None = None,
        run_deterministic: _DeterministicRunner | None = None,
        now: _Clock = _utc_timestamp,
    ) -> UnitGenerationRoundResult:
        """执行全部 pending generation Unit，并在完整 round Barrier 后返回。"""

        snapshot = controller.snapshot
        if snapshot.status != "active" or snapshot.phase != "generating_units":
            raise ValueError("Unit Scheduler 只接受 active/generating_units PlanningRun。")
        frozen_policy = UnitGenerationPolicy.model_validate(policy)
        frozen_contexts = {
            unit_id: UnitGenerationContext.model_validate(context)
            for unit_id, context in contexts.items()
        }
        frozen_global_feedback = {
            unit_id: tuple(ValidationIssue.model_validate(issue) for issue in issues)
            for unit_id, issues in (global_feedback_by_unit or {}).items()
        }
        scheduled_ids = tuple(
            unit_id
            for unit_id in snapshot.planning_unit_ids
            if snapshot.unit_states[unit_id].generation_status == "pending"
        )
        model_unit_ids = tuple(
            unit_id
            for unit_id in scheduled_ids
            if snapshot.unit_states[unit_id].generation_strategy == "model"
        )
        deterministic_unit_ids = tuple(
            unit_id
            for unit_id in scheduled_ids
            if snapshot.unit_states[unit_id].generation_strategy == "deterministic"
        )
        missing_contexts = tuple(
            unit_id for unit_id in model_unit_ids if unit_id not in frozen_contexts
        )
        if missing_contexts:
            raise ValueError(
                "Unit Scheduler 缺少 model Unit Context: " + ", ".join(missing_contexts)
            )
        if deterministic_unit_ids and run_deterministic is None:
            raise ValueError("Unit Scheduler 收到 deterministic Unit 但缺少确定性执行器。")

        # deterministic Candidate 在 worker pool 外串行提交，不占用模型并发槽位。
        try:
            for unit_id in deterministic_unit_ids:
                assert run_deterministic is not None
                await run_deterministic(controller.snapshot.unit_states[unit_id])
                if controller.snapshot.unit_states[unit_id].generation_status != "candidate_ready":
                    raise ValueError("确定性 Unit 必须在回调返回前进入 candidate_ready。")
        except asyncio.CancelledError:
            await controller.cancel(at=now())
            raise

        queue: asyncio.Queue[_QueuedAttempt | None] = asyncio.Queue()
        for unit_id in model_unit_ids:
            unit = controller.snapshot.unit_states[unit_id]
            queue.put_nowait(_QueuedAttempt(
                job=allocate_unit_attempt_job(
                    controller,
                    frozen_contexts[unit_id],
                    frozen_policy,
                ),
                global_feedback=frozen_global_feedback.get(unit_id, ()),
                local_feedback=_latest_local_feedback(unit),
            ))

        model_attempt_count = 0
        failures: list[Exception] = []
        fatal_error: Exception | None = None
        aborting = False
        workers: list[asyncio.Task[None]] = []

        def discard_queued_attempts() -> None:
            """丢弃尚未被 worker 取走的 Job，并结清 Queue Barrier 计数。"""

            while True:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                else:
                    queue.task_done()

        def stop_for_fatal(exc: Exception) -> None:
            """冻结 fatal、丢弃未派 Job，并 best-effort 取消其他 active worker。"""

            nonlocal fatal_error, aborting
            if fatal_error is not None:
                return
            fatal_error = exc
            aborting = True
            discard_queued_attempts()
            current = asyncio.current_task()
            for sibling in workers:
                if sibling is not current and not sibling.done():
                    sibling.cancel()

        async def worker() -> None:
            """逐个消费 Attempt；Local 内容失败的新 Job 重新追加到 FIFO 队尾。"""

            nonlocal model_attempt_count
            while True:
                queued = await queue.get()
                try:
                    if queued is None:
                        return
                    if aborting or controller.snapshot.status != "active":
                        continue
                    model_attempt_count += 1
                    try:
                        state = await run_unit_generation_attempt(
                            controller,
                            queued.job,
                            global_feedback=queued.global_feedback,
                            local_feedback=queued.local_feedback,
                            reuse_facts=reuse_facts,
                            unit_kind_rules=unit_kind_rules,
                            settings=settings,
                            generate_once=generate_once,
                            validate_candidate=validate_candidate,
                            now=now,
                        )
                    except Exception as exc:
                        if (
                            controller.snapshot.status == "failed"
                            and isinstance(
                                exc,
                                (
                                    UnitGenerationInfrastructureError,
                                    UnitGenerationPlatformError,
                                    UnitGenerationFatalError,
                                ),
                            )
                        ):
                            stop_for_fatal(exc)
                            return
                        if controller.snapshot.status != "active":
                            # fatal 已先提交时，取消无效或晚到 sibling 结果，不覆盖原始异常。
                            return
                        if not failures:
                            failures.append(exc)
                        continue
                    if aborting or controller.snapshot.status != "active":
                        # provider 可能吞掉 task cancellation 并返回晚到结果；gate 忽略后 worker 也必须退出。
                        return
                    if not aborting and controller.snapshot.status == "active" and state.generation_status == "pending":
                        queue.put_nowait(_QueuedAttempt(
                            job=allocate_unit_attempt_job(
                                controller,
                                frozen_contexts[state.unit_id],
                                frozen_policy,
                            ),
                            global_feedback=frozen_global_feedback.get(state.unit_id, ()),
                            local_feedback=_latest_local_feedback(state),
                        ))
                finally:
                    queue.task_done()

        worker_count = min(self._concurrency, len(model_unit_ids))
        workers = [
            asyncio.create_task(worker(), name=f"unit-generation-worker-{index + 1}")
            for index in range(worker_count)
        ]
        try:
            if workers:
                await queue.join()
                if fatal_error is None:
                    for _ in workers:
                        queue.put_nowait(None)
                    await asyncio.gather(*workers)
                else:
                    # sibling cancellation 只服务于当前 fatal；不得让 CancelledError 覆盖根因。
                    await asyncio.gather(*workers, return_exceptions=True)
            if fatal_error is not None:
                raise fatal_error
            if failures:
                raise failures[0]

            current = controller.snapshot
            incomplete = tuple(
                unit_id
                for unit_id in scheduled_ids
                if current.unit_states[unit_id].generation_status not in _TERMINAL_STATUSES
            )
            if incomplete:
                raise ValueError("Unit Scheduler 返回前仍有非终态 Unit: " + ", ".join(incomplete))
            completeness = await complete_generation_round(controller, now=now)
        except asyncio.CancelledError:
            # 先停止派发并提交 cancelled，再取消 active worker；晚到结果因此必定命中 T9.4 gate。
            aborting = True
            discard_queued_attempts()
            await controller.cancel(at=now())
            for active_worker in workers:
                if not active_worker.done():
                    active_worker.cancel()
            if workers:
                await asyncio.gather(*workers, return_exceptions=True)
            raise
        current = controller.snapshot
        return UnitGenerationRoundResult(
            planning_run_id=current.planning_run_id,
            model_unit_ids=model_unit_ids,
            deterministic_unit_ids=deterministic_unit_ids,
            candidate_ready_unit_ids=tuple(
                unit_id
                for unit_id in scheduled_ids
                if current.unit_states[unit_id].generation_status == "candidate_ready"
            ),
            round_exhausted_unit_ids=tuple(
                unit_id
                for unit_id in scheduled_ids
                if current.unit_states[unit_id].generation_status == "round_exhausted"
            ),
            model_attempt_count=model_attempt_count,
            completeness=completeness,
        )
