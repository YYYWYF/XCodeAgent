"""T9.4 active Attempt registry 与晚到结果隔离回归。"""

import tempfile
import unittest

from app.services import planning_run as transitions
from app.services.planning_run_controller import PlanningRunController
from app.services.planning_run_events import (
    CandidateInvalid,
    CandidateReady,
    RunCancelled,
    RunFailed,
    UnitAttemptStarted,
    UnitValidationStarted,
)
from tests.planning_run_fixtures import (
    AT,
    UNIT,
    candidate,
    identity,
    issue,
    repair_decision,
    run,
)


class AttemptResultAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    """验证只有活动 Run 的当前完整 AttemptIdentity 可以修改状态。"""

    def setUp(self) -> None:
        """为每个 acceptance 用例创建隔离的 Controller 工作区。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = {"workspace": directory.name}

    def _controller(self, snapshot) -> PlanningRunController:
        """用指定已提交领域快照创建无发布器的 Controller。"""

        return PlanningRunController(snapshot, self.workspace)

    async def test_current_result_is_accepted(self) -> None:
        """当前在途 Attempt 同时出现在 registry 和 gate，结果事件可提交。"""

        state = transitions.begin_generation(run(), at=AT)
        controller = self._controller(state)
        current = identity(state)
        await controller.apply(UnitAttemptStarted(identity=current, at=AT))

        self.assertEqual(dict(controller.active_attempts), {UNIT: current})
        self.assertTrue(controller.accepts_attempt(current))
        accepted = await controller.apply(UnitValidationStarted(identity=current, at=AT))
        self.assertEqual(accepted.unit_states[UNIT].generation_status, "validating")

    async def test_old_round_result_is_ignored(self) -> None:
        """新 generation round 已有在途 Attempt 时，上轮结果不得替换当前身份。"""

        state = transitions.begin_generation(run(), at=AT)
        old_identity = identity(state)
        state = transitions.mark_unit_generating(state, old_identity, at=AT)
        state = transitions.mark_unit_validating(state, old_identity, at=AT)
        old_candidate = candidate(state)
        state = transitions.record_candidate_ready(state, old_candidate, at=AT)
        state = transitions.begin_global_check(state, at=AT)
        state = transitions.begin_global_repair(
            state,
            repair_decision(issue(level="global")),
            at=AT,
        )
        controller = self._controller(state)
        current = identity(state)
        await controller.apply(UnitAttemptStarted(identity=current, at=AT))
        before = controller.snapshot

        self.assertFalse(controller.accepts_attempt(old_identity))
        ignored = await controller.apply(
            CandidateReady(candidate=old_candidate, at=AT)
        )
        self.assertIs(ignored, before)
        self.assertEqual(controller.active_attempts[UNIT], current)

    async def test_previous_local_attempt_result_is_ignored(self) -> None:
        """同一 round 启动下一 Local attempt 后，上一 attempt 的重复结果不得 apply。"""

        state = transitions.begin_generation(run(), at=AT)
        previous = identity(state)
        state = transitions.mark_unit_generating(state, previous, at=AT)
        invalid = candidate(state, valid=False)
        state = transitions.record_candidate_invalid(state, invalid, at=AT)
        controller = self._controller(state)
        current = identity(state)
        await controller.apply(UnitAttemptStarted(identity=current, at=AT))
        before = controller.snapshot

        self.assertFalse(controller.accepts_attempt(previous))
        ignored = await controller.apply(CandidateInvalid(candidate=invalid, at=AT))
        self.assertIs(ignored, before)
        self.assertEqual(controller.active_attempts[UNIT], current)

    async def test_superseded_unit_result_is_ignored(self) -> None:
        """Global repair 已 supersede Unit 旧 Candidate 后，旧 Attempt 不再活动。"""

        state = transitions.begin_generation(run(), at=AT)
        old_identity = identity(state)
        state = transitions.mark_unit_generating(state, old_identity, at=AT)
        state = transitions.mark_unit_validating(state, old_identity, at=AT)
        old_candidate = candidate(state)
        state = transitions.record_candidate_ready(state, old_candidate, at=AT)
        state = transitions.begin_global_repair(
            transitions.begin_global_check(state, at=AT),
            repair_decision(issue(level="global")),
            at=AT,
        )
        controller = self._controller(state)
        before = controller.snapshot

        self.assertEqual(dict(controller.active_attempts), {})
        self.assertFalse(controller.accepts_attempt(old_identity))
        ignored = await controller.apply(
            CandidateReady(candidate=old_candidate, at=AT)
        )
        self.assertIs(ignored, before)

    async def test_failed_and_cancelled_runs_ignore_late_results(self) -> None:
        """failed/cancelled 终态即使收到原在途身份也必须无写入忽略。"""

        for terminal_event in (
            RunFailed(issue=issue(retryable=False), at=AT),
            RunCancelled(at=AT),
        ):
            with self.subTest(event=type(terminal_event).__name__):
                state = transitions.begin_generation(run(), at=AT)
                controller = self._controller(state)
                current = identity(state)
                await controller.apply(UnitAttemptStarted(identity=current, at=AT))
                terminal = await controller.apply(terminal_event)

                self.assertEqual(dict(controller.active_attempts), {})
                self.assertFalse(controller.accepts_attempt(current))
                ignored = await controller.apply(
                    UnitValidationStarted(identity=current, at=AT)
                )
                self.assertIs(ignored, terminal)

    async def test_duplicate_callback_is_ignored_after_first_apply(self) -> None:
        """首次 Candidate apply 清理 registry 后，相同回调不增加 revision。"""

        state = transitions.begin_generation(run(), at=AT)
        controller = self._controller(state)
        current = identity(state)
        await controller.apply(UnitAttemptStarted(identity=current, at=AT))
        validated = await controller.apply(
            UnitValidationStarted(identity=current, at=AT)
        )
        duplicate_validation = await controller.apply(
            UnitValidationStarted(identity=current, at=AT)
        )
        result = candidate(controller.snapshot)
        accepted = await controller.apply(CandidateReady(candidate=result, at=AT))

        self.assertIs(duplicate_validation, validated)
        self.assertFalse(controller.accepts_attempt(current))
        duplicate = await controller.apply(CandidateReady(candidate=result, at=AT))
        self.assertIs(duplicate, accepted)
        self.assertEqual(duplicate.revision, accepted.revision)
        self.assertEqual(len(duplicate.candidates), 1)


if __name__ == "__main__":
    unittest.main()
