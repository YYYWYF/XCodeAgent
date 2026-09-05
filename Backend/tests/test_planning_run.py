"""T5.1 场景验收：纯状态、重试预算、Global reopen、supersede、fatal 与 cancel。"""

import unittest

from pydantic import ValidationError

from app.services import planning_run as sm
from tests.planning_run_fixtures import (
    AT, UNIT, candidate, exhausted, identity, invalid, issue, phases, ready, run, start, unit,
)


class PlanningRunTests(unittest.TestCase):
    def test_normal_lifecycle_remains_active_without_confirmation_status(self):
        """正常全链路只推进 phase，时间由调用方传入，无 succeeded 或确认状态。"""

        original = run()
        encoded = original.model_dump_json()
        state = sm.begin_generation(original, at=AT)
        state = ready(state)
        state = sm.begin_global_check(state, at=AT)
        state = sm.begin_assembly(state, at=AT)
        state = sm.begin_validation(state, at=AT)
        state = sm.begin_pending_persistence(state, at="2026-09-05T10:01:00Z")
        self.assertEqual((state.status, state.phase, state.revision), ("active", "persisting_pending", 8))
        self.assertEqual(state.global_repair_round, 0)
        self.assertEqual(state.updated_at, "2026-09-05T10:01:00Z")
        self.assertEqual(state.started_at, AT)
        self.assertNotIn("confirmation_status", state.model_dump())
        self.assertEqual(original.model_dump_json(), encoded)
        self.assertEqual(sm.PlanningRun.model_validate_json(state.model_dump_json()), state)

    def test_invalid_retry_retains_raw_candidates_and_latest_feedback(self):
        """内容失败保留原始任务和诊断；下一次成功不会覆盖失败记录。"""

        state = sm.begin_generation(run(), at=AT)
        state, first = start(state)
        bad = candidate(state, valid=False).model_copy(update={"tasks": ({"broken": True},)})
        state = sm.record_candidate_invalid(state, bad, at=AT)
        self.assertEqual(state.unit_states[UNIT].generation_status, "pending")
        self.assertEqual(state.unit_states[UNIT].current_issues, bad.validation_issues)
        state, second = start(state)
        with self.assertRaises(sm.IllegalPlanningTransition):
            sm.mark_unit_validating(state, first, at=AT)
        state = sm.mark_unit_validating(state, second, at=AT)
        good = candidate(state)
        state = sm.record_candidate_ready(state, good, at=AT)
        current = state.unit_states[UNIT]
        self.assertEqual((current.attempt_in_round, current.total_attempts), (2, 2))
        self.assertEqual(current.current_issues, ())
        self.assertEqual(current.latest_candidate_id, good.candidate_id)
        self.assertEqual(state.candidates[bad.candidate_id], bad)
        self.assertEqual(dict(state.candidates[bad.candidate_id].tasks[0]), {"broken": True})

    def test_exhausted_unit_can_reopen_without_becoming_failed(self):
        """三次无效后可以过 Barrier，但不能 Assembly；明确 Global 归因可重开。"""

        state = exhausted(sm.begin_generation(run(), at=AT))
        self.assertEqual((state.status, state.unit_states[UNIT].generation_status), ("active", "round_exhausted"))
        checking = sm.begin_global_check(state, at=AT)
        with self.assertRaises(sm.IllegalPlanningTransition):
            sm.begin_assembly(checking, at=AT)
        reopened = sm.begin_global_repair(checking, (issue(level="global"),), at=AT)
        current = reopened.unit_states[UNIT]
        self.assertEqual((current.generation_status, current.generation_round, current.attempt_in_round, current.total_attempts), ("pending", 2, 0, 3))
        self.assertEqual(current.round_history[0].generation_status, "round_exhausted")
        recovered = ready(reopened)
        self.assertEqual(recovered.unit_states[UNIT].total_attempts, 4)

    def test_global_reopen_supersedes_only_explicit_targets(self):
        """按 retry_unit_ids 重开，保留未受影响 Unit、retained 引用和旧候选正文。"""

        other = "page:customers"
        state = sm.begin_generation(run(unit(retained=("confirmed-task",)), unit(other)), at=AT)
        state = ready(ready(state), other)
        checking = sm.begin_global_check(state, at=AT)
        old_id = checking.unit_states[UNIT].latest_candidate_id
        old_candidate = checking.candidates[old_id]
        feedback = issue(UNIT, UNIT, level="global")
        reopened = sm.begin_global_repair(checking, (feedback, feedback), at=AT)
        current = reopened.unit_states[UNIT]
        self.assertEqual(reopened.global_repair_round, 1)
        self.assertEqual(reopened.revision, checking.revision + 1)
        self.assertEqual(reopened.global_issues, (feedback,))
        self.assertEqual(reopened.unit_states[other], checking.unit_states[other])
        self.assertEqual(current.retained_task_ids, ("confirmed-task",))
        self.assertEqual(current.participation, "reuse_and_generate")
        self.assertIsNone(current.latest_candidate_id)
        self.assertEqual(current.candidate_task_count, 0)
        self.assertEqual(reopened.candidates[old_id].status, "superseded")
        self.assertEqual(reopened.candidates[old_id].tasks, old_candidate.tasks)
        self.assertEqual(checking.candidates[old_id].status, "valid")
        failed_round = exhausted(reopened)
        self.assertIsNone(failed_round.unit_states[UNIT].latest_candidate_id)
        self.assertEqual(failed_round.candidates[old_id].status, "superseded")
        with self.assertRaises(sm.IllegalPlanningTransition):
            sm.record_candidate_ready(failed_round, old_candidate, at=AT)

    def test_superseded_id_cannot_be_rebound_to_new_identity(self):
        """即使伪装成新 round 的有效结果，也不能复用 superseded Candidate ID。"""

        checking = phases()["global_check"]
        old_id = checking.unit_states[UNIT].latest_candidate_id
        state = sm.begin_global_repair(checking, (issue(level="global"),), at=AT)
        state, attempt = start(state)
        state = sm.mark_unit_validating(state, attempt, at=AT)
        rebound = candidate(state).model_copy(update={"candidate_id": old_id})
        with self.assertRaises(sm.IllegalPlanningTransition):
            sm.record_candidate_ready(state, rebound, at=AT)
        self.assertEqual(state.candidates[old_id].status, "superseded")

    def test_global_limit_two_local_limit_three_and_maximum_nine(self):
        """完整执行三轮各三次，第三次 Global repair 被拒且不额外消耗预算。"""

        state = sm.begin_generation(run(), at=AT)
        for round_number in range(1, 4):
            state = exhausted(state)
            self.assertEqual(state.unit_states[UNIT].total_attempts, round_number * 3)
            state = sm.begin_global_check(state, at=AT)
            if round_number < 3:
                state = sm.begin_global_repair(state, (issue(level="global"),), at=AT)
        before = state.model_dump_json()
        with self.assertRaises(sm.IllegalPlanningTransition):
            sm.begin_global_repair(state, (issue(level="global"),), at=AT)
        self.assertEqual(state.model_dump_json(), before)
        failed = sm.fail(state, issue(level="global", retryable=False), at=AT)
        self.assertEqual((failed.status, failed.global_repair_round), ("failed", 2))

    def test_deterministic_candidate_never_spends_model_attempt_budget(self):
        """deterministic Candidate 具有合法独立身份，但三轮计数始终为零。"""

        target = "frontend:auth-guard"
        state = sm.begin_generation(run(unit(target, "deterministic")), at=AT)
        for round_number in range(1, 4):
            state = ready(state, target)
            current = state.unit_states[target]
            self.assertEqual((current.generation_round, current.attempt_in_round, current.total_attempts), (round_number, 0, 0))
            self.assertEqual(state.candidates[current.latest_candidate_id].identity.attempt_in_round, 1)
            state = sm.begin_global_check(state, at=AT)
            if round_number < 3:
                state = sm.begin_global_repair(state, (issue(target, level="global"),), at=AT)
        self.assertEqual(sum(item.status == "superseded" for item in state.candidates.values()), 2)

    def test_no_generation_participants_pass_without_tasks_or_model_attempts(self):
        """shell/structural/reuse-only 无 Candidate 也可过完整阶段链，且不能调度或重开。"""

        fixtures = (
            unit("frontend:shell", "prerequisite_only"), unit("application:root", "structural_only"),
            unit("app:integration", "structural_only"), unit("page:reused", "reuse_only"),
        )
        state = sm.begin_generation(run(*fixtures), at=AT)
        for current in fixtures:
            with self.subTest(unit=current.unit_id), self.assertRaises(sm.IllegalPlanningTransition):
                sm.mark_unit_generating(state, identity(state, current.unit_id), at=AT)
        state = sm.begin_global_check(state, at=AT)
        for current in fixtures:
            with self.subTest(reopen=current.unit_id), self.assertRaises(sm.IllegalPlanningTransition):
                sm.begin_global_repair(state, (issue(current.unit_id, level="global"),), at=AT)
        state = sm.begin_pending_persistence(sm.begin_validation(sm.begin_assembly(state, at=AT), at=AT), at=AT)
        self.assertEqual(state.planning_unit_ids, ())
        self.assertEqual(dict(state.candidates), {})
        self.assertTrue(all(item.generation_status == "not_required" for item in state.unit_states.values()))

    def test_fatal_and_cancel_abort_inflight_and_preserve_completed_work(self):
        """终态撤销所有在途身份和待生成 Unit，同时保留已完成 Candidate 供诊断。"""

        targets = (UNIT, "page:pending", "page:validating", "page:ready")
        state = sm.begin_generation(run(*(unit(target) for target in targets)), at=AT)
        state, attempt = start(state)
        late_candidate = candidate(state)
        state, validating = start(state, targets[2])
        state = sm.mark_unit_validating(state, validating, at=AT)
        state = ready(state, targets[3])
        terminal_states = (
            sm.fail(state, issue(level="system", category="infrastructure", retryable=False), at=AT),
            sm.cancel(state, at=AT),
        )
        for terminal in terminal_states:
            with self.subTest(status=terminal.status):
                self.assertEqual(terminal.global_repair_round, 0)
                self.assertEqual(terminal.candidates, state.candidates)
                for target in targets[:3]:
                    self.assertEqual(terminal.unit_states[target].generation_status, "aborted")
                    self.assertIsNone(terminal.unit_states[target].expected_identity)
                    self.assertEqual(terminal.unit_states[target].total_attempts, state.unit_states[target].total_attempts)
                for transition, args in (
                    (sm.mark_unit_validating, (attempt,)), (sm.record_candidate_ready, (late_candidate,)),
                    (sm.cancel, ()), (sm.begin_generation, ()),
                ):
                    with self.assertRaises(sm.IllegalPlanningTransition):
                        transition(terminal, *args, at=AT)

    def test_snapshots_are_deeply_frozen_and_exports_are_detached(self):
        """外部输入、JSON 副本和嵌套候选都不能绕过转换直接改变当前快照。"""

        state = invalid(sm.begin_generation(run(), at=AT))
        candidate_id = next(iter(state.candidates))
        with self.assertRaises(ValidationError):
            state.status = "failed"
        with self.assertRaises(TypeError):
            state.unit_states[UNIT] = unit()
        with self.assertRaises(TypeError):
            state.candidates[candidate_id].tasks[0]["details"]["items"] = ()
        with self.assertRaises(TypeError):
            state.unit_states[UNIT].current_issues[0].details["nested"][0]["source"] = "changed"
        exported = state.model_dump(mode="json")
        exported["candidates"].clear()
        exported["unit_states"][UNIT]["current_issues"][0]["retry_unit_ids"].clear()
        self.assertEqual(len(state.candidates), 1)
        self.assertEqual(state.unit_states[UNIT].current_issues[0].retry_unit_ids, (UNIT,))
