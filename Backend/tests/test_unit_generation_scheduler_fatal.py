"""T9.3 concurrent Unit Scheduler 基础设施 fatal abort 回归。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from typing import Any

from app.services import planning_run as transitions
from app.services.planning_issues import ValidationIssue
from app.services.planning_run_controller import PlanningRunController
from app.services.unit_generation import UnitGenerationInfrastructureError
from app.services.unit_generation_contracts import (
    UnitGenerationAttemptResult,
    UnitGenerationContext,
    UnitGenerationPolicy,
)
from app.services.unit_generation_scheduler import UnitGenerationScheduler
from app.workspace.task_documents import build_task_plan_pending_json_path
from tests.planning_run_fixtures import AT, run, unit
from tests.test_unit_candidate_validator import _context
from tests.test_unit_generation_contracts import _policy_payload


def _accept_candidate(
    _context_value: UnitGenerationContext,
    _tasks: Sequence[Mapping[str, Any]],
    _reuse_facts: Mapping[str, Any] | None,
) -> list[ValidationIssue]:
    """让 fatal 调度测试只观察 abort 协议，不重复业务校验。"""

    return []


def _success(job) -> UnitGenerationAttemptResult:
    """构造绑定当前 Attempt 的有效模型结果。"""

    return UnitGenerationAttemptResult(
        identity=job.identity,
        input_fingerprint=job.context.input_fingerprint,
        raw_response="mock success",
        tasks=({
            "id": f"task-{job.identity.unit_id}",
            "unit_id": job.identity.unit_id,
        },),
    )


class UnitGenerationSchedulerFatalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        """创建五个 model Unit 的隔离 Run、Context 和固定策略。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = {"workspace": directory.name}
        unit_ids = tuple(f"page:{name}" for name in "abcde")
        initial = transitions.begin_generation(
            run(*(unit(unit_id) for unit_id in unit_ids)),
            at=AT,
        )
        self.controller = PlanningRunController(initial, self.workspace)
        self.contexts = {
            unit_id: _context(unit_id).model_copy(update={
                "planning_run_id": initial.planning_run_id,
                "input_fingerprint": initial.input_fingerprint,
                "base_confirmed_plan_digest": initial.base_confirmed_plan_digest,
                "build_execution_scope": initial.build_execution_scope,
            })
            for unit_id in unit_ids
        }
        self.policy = UnitGenerationPolicy(**_policy_payload())

    async def _run(self, generate_once) -> None:
        """以三个 worker 执行一次 round，并保留 fatal 异常给测试断言。"""

        await UnitGenerationScheduler(concurrency=3).run_round(
            self.controller,
            self.contexts,
            self.policy,
            generate_once=generate_once,
            validate_candidate=_accept_candidate,
            now=lambda: AT,
        )

    async def test_b_fatal_cancels_active_a_c_and_never_dispatches_d_e(self) -> None:
        """B fatal 时停止队列、取消 active sibling，失败 Run 不留下 pending Unit/Plan。"""

        active = {"page:a": asyncio.Event(), "page:c": asyncio.Event()}
        never = asyncio.Event()
        starts: list[str] = []
        cancelled: list[str] = []

        async def generate(job, **_kwargs):
            """让 A/C 保持 active，待 B 观察到二者后触发基础设施 fatal。"""

            unit_id = job.identity.unit_id
            starts.append(unit_id)
            if unit_id == "page:b":
                await active["page:a"].wait()
                await active["page:c"].wait()
                raise UnitGenerationInfrastructureError(
                    identity=job.identity,
                    stage="model_invoke",
                    cause=ConnectionError("offline"),
                )
            if unit_id in active:
                active[unit_id].set()
                try:
                    await never.wait()
                except asyncio.CancelledError:
                    cancelled.append(unit_id)
                    raise
            return _success(job)

        with self.assertRaises(UnitGenerationInfrastructureError) as caught:
            await asyncio.wait_for(self._run(generate), 1)

        snapshot = self.controller.snapshot
        self.assertEqual(caught.exception.identity.unit_id, "page:b")
        self.assertCountEqual(starts, ("page:a", "page:b", "page:c"))
        self.assertCountEqual(cancelled, ("page:a", "page:c"))
        self.assertEqual(snapshot.status, "failed")
        self.assertEqual(snapshot.failure.category, "infrastructure")
        self.assertEqual(snapshot.candidates, {})
        self.assertTrue(all(
            state.generation_status == "aborted"
            for state in snapshot.unit_states.values()
        ))
        self.assertEqual(snapshot.unit_states["page:d"].total_attempts, 0)
        self.assertEqual(snapshot.unit_states["page:e"].total_attempts, 0)
        self.assertFalse(build_task_plan_pending_json_path(self.workspace).exists())

    async def test_cancel_resistant_late_sibling_result_is_ignored(self) -> None:
        """A 吞掉取消并晚到成功时不形成 Candidate，最终仍传播 B 的原始 fatal。"""

        a_started = asyncio.Event()
        never = asyncio.Event()
        late_result_returned = asyncio.Event()
        fatal: UnitGenerationInfrastructureError | None = None

        async def generate(job, **_kwargs):
            """模拟不完全遵守取消的 provider wrapper，以覆盖 fatal 后晚到结果。"""

            nonlocal fatal
            if job.identity.unit_id == "page:a":
                a_started.set()
                try:
                    await never.wait()
                except asyncio.CancelledError:
                    late_result_returned.set()
                    return _success(job)
            if job.identity.unit_id == "page:b":
                await a_started.wait()
                fatal = UnitGenerationInfrastructureError(
                    identity=job.identity,
                    stage="model_invoke",
                    cause=TimeoutError("provider timeout"),
                )
                raise fatal
            await never.wait()

        with self.assertRaises(UnitGenerationInfrastructureError) as caught:
            await asyncio.wait_for(self._run(generate), 1)

        snapshot = self.controller.snapshot
        self.assertIs(caught.exception, fatal)
        self.assertTrue(late_result_returned.is_set())
        self.assertEqual(snapshot.status, "failed")
        self.assertEqual(snapshot.candidates, {})
        self.assertEqual(snapshot.unit_states["page:a"].generation_status, "aborted")
        self.assertIsNone(snapshot.unit_states["page:a"].expected_identity)


if __name__ == "__main__":
    unittest.main()
