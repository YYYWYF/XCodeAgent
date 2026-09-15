"""T6.2 generation round Barrier 与 Candidate completeness 回归。"""

from __future__ import annotations

import tempfile
import unittest

from app.services import planning_run as transitions
from app.services.global_planning_validation import (
    CandidateCompletenessResult,
    check_candidate_completeness,
)
from app.services.planning_run_controller import PlanningRunController
from app.services.planning_run_events import (
    CandidateReady,
    UnitAttemptStarted,
    UnitValidationStarted,
)
from app.services.unit_generation_orchestrator import (
    GenerationRoundBarrierPending,
    complete_generation_round,
)
from tests.planning_run_fixtures import (
    AT,
    candidate,
    exhausted,
    identity,
    ready,
    run,
    unit,
)


class CandidateCompletenessBarrierTests(unittest.IsolatedAsyncioTestCase):
    def _controller(self, state) -> PlanningRunController:
        """为给定内存快照创建独立单写者，避免测试间共享持久化状态。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return PlanningRunController(state, {"workspace": directory.name})

    async def _ready_current_unit(
        self,
        controller: PlanningRunController,
        unit_id: str,
    ) -> None:
        """通过真实 Controller 事件把一个 pending Unit 推进到 candidate_ready。"""

        attempt = identity(controller.snapshot, unit_id)
        await controller.apply(UnitAttemptStarted(identity=attempt, at=AT))
        await controller.apply(UnitValidationStarted(identity=attempt, at=AT))
        await controller.apply(CandidateReady(
            candidate=candidate(
                controller.snapshot,
                unit_id=unit_id,
                attempt=attempt,
            ),
            at=AT,
        ))

    async def test_all_ready_waits_for_every_scheduled_unit(self) -> None:
        """一个 Unit ready 不越过 Barrier，全部 ready 后才进入 global_check。"""

        initial = transitions.begin_generation(
            run(unit("page:a"), unit("page:b")),
            at=AT,
        )
        first_ready = ready(initial, "page:a")
        controller = self._controller(first_ready)

        with self.assertRaises(GenerationRoundBarrierPending) as caught:
            await complete_generation_round(controller, now=lambda: AT)
        self.assertEqual(caught.exception.pending_unit_ids, ("page:b",))
        self.assertEqual(controller.snapshot.phase, "generating_units")

        await self._ready_current_unit(controller, "page:b")
        result = await complete_generation_round(controller, now=lambda: AT)

        self.assertIsInstance(result, CandidateCompletenessResult)
        self.assertTrue(result.complete)
        self.assertEqual(result.ready_unit_ids, ("page:a", "page:b"))
        self.assertEqual(result.missing_unit_ids, ())
        self.assertEqual(result.issues, ())
        self.assertEqual(controller.snapshot.phase, "global_check")

    async def test_one_exhausted_becomes_attributable_missing_issue(self) -> None:
        """所有 Unit 已终态时通过 Barrier，但 exhausted Unit 使 completeness 失败。"""

        state = transitions.begin_generation(
            run(unit("page:a"), unit("page:b")),
            at=AT,
        )
        state = ready(state, "page:a")
        state = exhausted(state, "page:b")
        controller = self._controller(state)

        result = await complete_generation_round(controller, now=lambda: AT)

        self.assertFalse(result.complete)
        self.assertEqual(result.ready_unit_ids, ("page:a",))
        self.assertEqual(result.missing_unit_ids, ("page:b",))
        self.assertEqual(len(result.issues), 1)
        issue = result.issues[0]
        self.assertEqual(issue.code, "GLOBAL_CANDIDATE_MISSING")
        self.assertEqual(issue.unit_ids, ("page:b",))
        self.assertEqual(issue.retry_unit_ids, ("page:b",))
        self.assertTrue(issue.retryable)
        self.assertEqual(controller.snapshot.phase, "global_check")

    async def test_reuse_only_mix_does_not_require_candidate(self) -> None:
        """reuse_only Unit 不进入 ready/missing 集合，也不阻断模型 Candidate。"""

        state = transitions.begin_generation(
            run(unit("page:a"), unit("page:retained", "reuse_only")),
            at=AT,
        )
        state = ready(state, "page:a")

        result = check_candidate_completeness(state.unit_states)

        self.assertTrue(result.complete)
        self.assertEqual(result.ready_unit_ids, ("page:a",))
        self.assertEqual(result.missing_unit_ids, ())

    async def test_shell_mix_does_not_require_candidate(self) -> None:
        """frontend:shell prerequisite_only 只表达前置能力，不产生 Candidate。"""

        state = transitions.begin_generation(
            run(unit("page:a"), unit("frontend:shell", "prerequisite_only")),
            at=AT,
        )
        state = ready(state, "page:a")

        result = check_candidate_completeness(state.unit_states)

        self.assertTrue(result.complete)
        self.assertEqual(result.ready_unit_ids, ("page:a",))
        self.assertNotIn("frontend:shell", result.ready_unit_ids)

    async def test_deterministic_auth_requires_ready_candidate(self) -> None:
        """deterministic auth Unit 与 model Unit 一样必须 ready，且不消耗模型 Attempt。"""

        state = transitions.begin_generation(
            run(unit("frontend:auth-guard", "deterministic")),
            at=AT,
        )
        controller = self._controller(state)
        with self.assertRaises(GenerationRoundBarrierPending):
            await complete_generation_round(controller, now=lambda: AT)

        await self._ready_current_unit(controller, "frontend:auth-guard")
        result = await complete_generation_round(controller, now=lambda: AT)

        self.assertTrue(result.complete)
        self.assertEqual(result.ready_unit_ids, ("frontend:auth-guard",))
        self.assertEqual(
            controller.snapshot.unit_states["frontend:auth-guard"].total_attempts,
            0,
        )

    async def test_zero_planning_model_units_is_complete(self) -> None:
        """仅复用、前置和结构 Unit 时空 Barrier 可通过且不伪造 Candidate。"""

        state = transitions.begin_generation(
            run(
                unit("page:retained", "reuse_only"),
                unit("frontend:shell", "prerequisite_only"),
                unit("application:root", "structural_only"),
                unit("app:integration", "structural_only"),
            ),
            at=AT,
        )
        controller = self._controller(state)

        result = await complete_generation_round(controller, now=lambda: AT)

        self.assertTrue(result.complete)
        self.assertEqual(result.ready_unit_ids, ())
        self.assertEqual(result.missing_unit_ids, ())
        self.assertEqual(result.issues, ())
        self.assertEqual(controller.snapshot.phase, "global_check")


if __name__ == "__main__":
    unittest.main()
