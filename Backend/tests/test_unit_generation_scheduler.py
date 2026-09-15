"""T9.2 UnitAttempt FIFO Queue、有限 Worker Pool 与 round Barrier 回归。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from typing import Any

from app.services import planning_run as transitions
from app.services.planning_issues import ValidationIssue
from app.services.planning_run_contracts import UnitRunState
from app.services.planning_run_controller import PlanningRunController
from app.services.planning_run_events import (
    CandidateReady,
    UnitAttemptStarted,
    UnitValidationStarted,
)
from app.services.unit_generation_contracts import (
    AttemptIdentity,
    CandidateAttempt,
    UnitGenerationAttemptResult,
    UnitGenerationContext,
    UnitGenerationPolicy,
)
from app.services.unit_generation_scheduler import UnitGenerationScheduler
from tests.planning_run_fixtures import AT, run, unit
from tests.test_unit_candidate_validator import _context
from tests.test_unit_generation_contracts import _policy_payload


def _accept_candidate(
    _context_value: UnitGenerationContext,
    _tasks: Sequence[Mapping[str, Any]],
    _reuse_facts: Mapping[str, Any] | None,
) -> list[ValidationIssue]:
    """让调度测试只观察队列和状态提交，不重复测试 Local 业务规则。"""

    return []


def _local_issue(unit_id: str) -> ValidationIssue:
    """构造只允许当前 Unit 进入下一 Local attempt 的内容问题。"""

    return ValidationIssue(
        code="TEST_LOCAL_RETRY",
        level="unit",
        category="generation",
        unit_ids=(unit_id,),
        retry_unit_ids=(unit_id,),
        retryable=True,
        message="测试要求当前 Unit 重试。",
    )


def _success(job) -> UnitGenerationAttemptResult:
    """为当前 Attempt 返回无需业务校验修正的非空 Candidate。"""

    return UnitGenerationAttemptResult(
        identity=job.identity,
        input_fingerprint=job.context.input_fingerprint,
        raw_response="mock success",
        tasks=({
            "id": f"task-{job.identity.unit_id}-{job.identity.attempt_in_round}",
            "unit_id": job.identity.unit_id,
        },),
    )


def _invalid(job) -> UnitGenerationAttemptResult:
    """为当前 Attempt 返回可重试内容问题，跳过后续 Validator。"""

    return UnitGenerationAttemptResult(
        identity=job.identity,
        input_fingerprint=job.context.input_fingerprint,
        raw_response="mock invalid",
        tasks=(),
        validation_issues=(_local_issue(job.identity.unit_id),),
    )


class UnitGenerationSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def _setup(
        self,
        unit_ids: Sequence[str],
        *,
        deterministic_ids: Sequence[str] = (),
    ) -> tuple[
        PlanningRunController,
        dict[str, UnitGenerationContext],
        UnitGenerationPolicy,
    ]:
        """创建 generation phase Controller 与每个 model Unit 的冻结 Context。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        deterministic = set(deterministic_ids)
        initial = transitions.begin_generation(run(*(
            unit(
                unit_id,
                strategy="deterministic" if unit_id in deterministic else "model",
            )
            for unit_id in unit_ids
        )), at=AT)
        controller = PlanningRunController(initial, {"workspace": directory.name})
        contexts = {
            unit_id: _context(unit_id).model_copy(update={
                "planning_run_id": initial.planning_run_id,
                "input_fingerprint": initial.input_fingerprint,
                "base_confirmed_plan_digest": initial.base_confirmed_plan_digest,
                "build_execution_scope": initial.build_execution_scope,
            })
            for unit_id in unit_ids
            if unit_id not in deterministic
        }
        return controller, contexts, UnitGenerationPolicy(**_policy_payload())

    async def _run(
        self,
        scheduler: UnitGenerationScheduler,
        controller: PlanningRunController,
        contexts: Mapping[str, UnitGenerationContext],
        policy: UnitGenerationPolicy,
        generate_once,
        *,
        run_deterministic=None,
    ):
        """以固定时钟和无业务失败 Validator 执行一次 Scheduler round。"""

        return await scheduler.run_round(
            controller,
            contexts,
            policy,
            generate_once=generate_once,
            validate_candidate=_accept_candidate,
            run_deterministic=run_deterministic,
            now=lambda: AT,
        )

    async def test_one_two_three_and_five_units_respect_max_active_sessions(self) -> None:
        """1/2/3/5 个 Unit 的实际活跃模型数分别不超过 min(N, 3)。"""

        for count in (1, 2, 3, 5):
            with self.subTest(unit_count=count):
                unit_ids = tuple(f"page:{index}" for index in range(count))
                controller, contexts, policy = self._setup(unit_ids)
                scheduler = UnitGenerationScheduler(concurrency=5)
                active = 0
                max_active = 0
                release = asyncio.Event()
                target_active = min(count, 3)

                async def generate(job, **_kwargs):
                    """用门闩让首批 worker 同时停在模型 session 内并记录峰值。"""

                    nonlocal active, max_active
                    active += 1
                    max_active = max(max_active, active)
                    if active == target_active:
                        release.set()
                    try:
                        await asyncio.wait_for(release.wait(), 1)
                        await asyncio.sleep(0)
                        return _success(job)
                    finally:
                        active -= 1

                result = await self._run(
                    scheduler,
                    controller,
                    contexts,
                    policy,
                    generate,
                )

                self.assertEqual(scheduler.concurrency, 3)
                self.assertEqual(max_active, target_active)
                self.assertEqual(result.model_attempt_count, count)
                self.assertTrue(result.completeness.complete)
                self.assertEqual(controller.snapshot.phase, "global_check")

    async def test_fast_failure_requeues_after_waiting_d_and_e(self) -> None:
        """A 快速失败后 A2 位于 D/E 之后，不能让当前 Unit 饿死队尾工作。"""

        unit_ids = tuple(f"page:{name}" for name in "abcde")
        controller, contexts, policy = self._setup(unit_ids)
        release_slow = asyncio.Event()
        starts: list[tuple[str, int]] = []

        async def generate(job, **_kwargs):
            """阻塞 B/C，让处理 A 的 worker 展示 D、E、A2 的 FIFO 顺序。"""

            key = (job.identity.unit_id, job.identity.attempt_in_round)
            starts.append(key)
            if job.identity.unit_id in {"page:b", "page:c"}:
                await release_slow.wait()
            if key == ("page:a", 1):
                return _invalid(job)
            if key == ("page:a", 2):
                release_slow.set()
            return _success(job)

        result = await self._run(
            UnitGenerationScheduler(concurrency=3),
            controller,
            contexts,
            policy,
            generate,
        )

        self.assertLess(starts.index(("page:d", 1)), starts.index(("page:a", 2)))
        self.assertLess(starts.index(("page:e", 1)), starts.index(("page:a", 2)))
        self.assertEqual(result.model_attempt_count, 6)
        self.assertTrue(result.completeness.complete)

    async def test_local_retry_jobs_return_to_fifo_tail(self) -> None:
        """单 worker 下 A/B 的第二次 Attempt 精确排在尚未运行的 C 之后。"""

        unit_ids = ("page:a", "page:b", "page:c")
        controller, contexts, policy = self._setup(unit_ids)
        starts: list[tuple[str, int]] = []
        feedback: list[tuple[str, int, tuple[str, ...]]] = []

        async def generate(job, **kwargs):
            """记录出队顺序和入队冻结的最新 Local feedback。"""

            key = (job.identity.unit_id, job.identity.attempt_in_round)
            starts.append(key)
            feedback.append((
                *key,
                tuple(issue.code for issue in kwargs["local_feedback"]),
            ))
            if job.identity.unit_id in {"page:a", "page:b"} and job.identity.attempt_in_round == 1:
                return _invalid(job)
            return _success(job)

        await self._run(
            UnitGenerationScheduler(concurrency=1),
            controller,
            contexts,
            policy,
            generate,
        )

        self.assertEqual(starts, [
            ("page:a", 1),
            ("page:b", 1),
            ("page:c", 1),
            ("page:a", 2),
            ("page:b", 2),
        ])
        self.assertEqual(feedback[-2][2], ("TEST_LOCAL_RETRY",))
        self.assertEqual(feedback[-1][2], ("TEST_LOCAL_RETRY",))

    async def test_deterministic_unit_never_enters_model_worker_pool(self) -> None:
        """deterministic 与 model 混合时仅两个 model Unit 消耗 worker/attempt。"""

        deterministic_id = "frontend:auth-guard"
        unit_ids = ("page:a", deterministic_id, "page:b")
        controller, contexts, policy = self._setup(
            unit_ids,
            deterministic_ids=(deterministic_id,),
        )
        model_calls: list[str] = []
        deterministic_calls: list[str] = []

        async def generate(job, **_kwargs):
            """记录真正进入模型 worker 的 Unit。"""

            model_calls.append(job.identity.unit_id)
            await asyncio.sleep(0)
            return _success(job)

        async def run_deterministic(state: UnitRunState) -> None:
            """在 worker pool 外按既有 Controller 事件提交确定性 Candidate。"""

            deterministic_calls.append(state.unit_id)
            identity = AttemptIdentity.allocate(
                planning_run_id=controller.snapshot.planning_run_id,
                unit_id=state.unit_id,
                generation_round=state.generation_round,
                attempt_in_round=1,
            )
            await controller.apply(UnitAttemptStarted(identity=identity, at=AT))
            await controller.apply(UnitValidationStarted(identity=identity, at=AT))
            await controller.apply(CandidateReady(candidate=CandidateAttempt(
                identity=identity,
                input_fingerprint=controller.snapshot.input_fingerprint,
                status="valid",
                tasks=({"id": "task-auth", "unit_id": state.unit_id},),
            ), at=AT))

        result = await self._run(
            UnitGenerationScheduler(concurrency=2),
            controller,
            contexts,
            policy,
            generate,
            run_deterministic=run_deterministic,
        )

        self.assertEqual(deterministic_calls, [deterministic_id])
        self.assertCountEqual(model_calls, ["page:a", "page:b"])
        self.assertEqual(result.model_unit_ids, ("page:a", "page:b"))
        self.assertEqual(result.deterministic_unit_ids, (deterministic_id,))
        self.assertEqual(result.model_attempt_count, 2)
        deterministic = controller.snapshot.unit_states[deterministic_id]
        self.assertEqual((deterministic.attempt_in_round, deterministic.total_attempts), (0, 0))

    async def test_round_barrier_waits_for_ready_and_exhausted_model_units(self) -> None:
        """一个 Unit 已 exhausted 时，另一个仍 active 会阻止 Barrier 提前返回。"""

        controller, contexts, policy = self._setup(("page:a", "page:b"))
        release_a = asyncio.Event()
        a_started = asyncio.Event()

        async def generate(job, **_kwargs):
            """保持 A 在途，同时让 B 连续三次内容失败直至 exhausted。"""

            if job.identity.unit_id == "page:a":
                a_started.set()
                await release_a.wait()
                return _success(job)
            return _invalid(job)

        async def wait_until_b_exhausted() -> None:
            """等待 Controller 提交 B 的 round_exhausted 终态。"""

            while controller.snapshot.unit_states["page:b"].generation_status != "round_exhausted":
                await asyncio.sleep(0)

        round_task = asyncio.create_task(self._run(
            UnitGenerationScheduler(concurrency=2),
            controller,
            contexts,
            policy,
            generate,
        ))
        await asyncio.wait_for(a_started.wait(), 1)
        await asyncio.wait_for(wait_until_b_exhausted(), 1)
        self.assertFalse(round_task.done())
        self.assertEqual(controller.snapshot.phase, "generating_units")
        release_a.set()
        result = await asyncio.wait_for(round_task, 1)

        self.assertFalse(result.completeness.complete)
        self.assertEqual(result.round_exhausted_unit_ids, ("page:b",))
        self.assertEqual(result.model_attempt_count, 4)
        self.assertEqual(controller.snapshot.phase, "global_check")


if __name__ == "__main__":
    unittest.main()
