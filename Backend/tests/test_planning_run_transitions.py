"""T5.1 合法/非法转换矩阵与边界输入的原子拒绝验证。"""

import unittest

from pydantic import ValidationError

from app.services import planning_run as sm
from tests.planning_run_fixtures import (
    AT, UNIT, candidate, exhausted, identity, invalid, issue, phases, ready, run, start, unit,
)


class PlanningRunTransitionTests(unittest.TestCase):
    def assert_transition(self, state, transition, args, allowed):
        """验证合法转换只增加一次 revision，非法转换不会部分改变输入。"""

        before = state.model_dump_json()
        if allowed:
            result = transition(state, *args, at=AT)
            self.assertEqual(result.revision, state.revision + 1)
        else:
            with self.assertRaises(sm.IllegalPlanningTransition):
                transition(state, *args, at=AT)
        self.assertEqual(state.model_dump_json(), before)

    def test_complete_run_phase_transition_matrix(self):
        """六个 phase 对全部 Run 级事件穷举，禁止跳阶段、重复进入或逆向流转。"""

        events = (
            (sm.begin_generation, (), {"preparing"}),
            (sm.begin_global_check, (), {"generating_units"}),
            (sm.begin_assembly, (), {"global_check"}),
            (sm.begin_validation, (), {"assembling"}),
            (sm.begin_pending_persistence, (), {"validating"}),
            (sm.begin_global_repair, ((issue(level="global"),),), {"global_check", "assembling", "validating"}),
            (sm.fail, (issue(retryable=False),), set(phases())),
            (sm.cancel, (), set(phases())),
        )
        for phase, state in phases().items():
            for transition, args, sources in events:
                with self.subTest(phase=phase, event=transition.__name__):
                    self.assert_transition(state, transition, args, phase in sources)

    def test_failed_cancelled_are_terminal_in_every_phase_for_every_event(self):
        """两个终态在所有 phase 拒绝所有 Run/Unit 事件，包括重复终止与晚到结果。"""

        for phase, active in phases().items():
            attempt = identity(active)
            result = candidate(active, attempt=attempt)
            events = (
                (sm.begin_generation, ()), (sm.begin_global_check, ()), (sm.begin_assembly, ()),
                (sm.begin_validation, ()), (sm.begin_pending_persistence, ()),
                (sm.begin_global_repair, ((issue(level="global"),),)),
                (sm.mark_unit_generating, (attempt,)), (sm.mark_unit_validating, (attempt,)),
                (sm.record_candidate_ready, (result,)),
                (sm.record_candidate_invalid, (result.model_copy(update={"status": "invalid", "validation_issues": (issue(),)}),)),
                (sm.mark_round_exhausted, (UNIT,)), (sm.fail, (issue(retryable=False),)), (sm.cancel, ()),
            )
            for terminal in (sm.fail(active, issue(retryable=False), at=AT), sm.cancel(active, at=AT)):
                for transition, args in events:
                    with self.subTest(phase=phase, status=terminal.status, event=transition.__name__):
                        self.assert_transition(terminal, transition, args, False)

    def test_complete_unit_status_transition_matrix(self):
        """穷举七种 Unit 状态，并区分 pending 的未耗尽与已耗尽预算。"""

        pending = sm.begin_generation(run(), at=AT)
        generating, expected = start(pending)
        validating = sm.mark_unit_validating(generating, expected, at=AT)
        pending_three = invalid(invalid(invalid(pending)))
        fixtures = {
            "pending": pending, "pending_three": pending_three, "generating": generating,
            "validating": validating, "candidate_ready": ready(pending),
            "round_exhausted": exhausted(pending), "aborted": sm.cancel(generating, at=AT),
            "not_required": sm.begin_generation(run(unit(strategy="reuse_only")), at=AT),
        }
        for status, state in fixtures.items():
            attempt = state.unit_states[UNIT].expected_identity or identity(state)
            events = (
                (sm.mark_unit_generating, (identity(state),), {"pending"}),
                (sm.mark_unit_validating, (attempt,), {"generating"}),
                (sm.record_candidate_invalid, (candidate(state, valid=False, attempt=attempt),), {"generating", "validating"}),
                (sm.record_candidate_ready, (candidate(state, attempt=attempt),), {"validating"}),
                (sm.mark_round_exhausted, (UNIT,), {"pending_three"}),
                (sm.begin_global_check, (), {"candidate_ready", "round_exhausted", "not_required"}),
            )
            for transition, args, sources in events:
                with self.subTest(status=status, event=transition.__name__):
                    self.assert_transition(state, transition, args, status in sources)

    def test_candidate_status_transition_matrix_and_recorded_candidates_are_terminal(self):
        """只接纳对应结论的新 Candidate；valid/invalid/superseded 记录不能再次提交。"""

        state, attempt = start(sm.begin_generation(run(), at=AT))
        state = sm.mark_unit_validating(state, attempt, at=AT)
        for status in ("valid", "invalid", "superseded"):
            result = candidate(state, valid=status == "valid").model_copy(update={"status": status})
            for transition, accepted in ((sm.record_candidate_ready, "valid"), (sm.record_candidate_invalid, "invalid")):
                with self.subTest(candidate_status=status, event=transition.__name__):
                    self.assert_transition(state, transition, (result,), status == accepted)
        for valid in (True, False):
            result = candidate(state, valid=valid)
            transition = sm.record_candidate_ready if valid else sm.record_candidate_invalid
            recorded = transition(state, result, at=AT)
            for again in (sm.record_candidate_ready, sm.record_candidate_invalid):
                with self.subTest(recorded=valid, repeat=again.__name__):
                    self.assert_transition(recorded, again, (result,), False)

    def test_wrong_attempt_identity_and_fingerprint_are_rejected_atomically(self):
        """拒绝跨 Run/Unit、旧轮次、错误序号、非预期 attempt_id 及不同输入指纹。"""

        pending = sm.begin_generation(run(), at=AT)
        state, attempt = start(pending)
        changes = (
            {"planning_run_id": "other-run"}, {"unit_id": "page:other"},
            {"generation_round": 2}, {"attempt_in_round": 2}, {"attempt_id": "attempt-" + "f" * 32},
        )
        for change in changes:
            wrong = attempt.model_copy(update=change)
            with self.subTest(change=change):
                self.assert_transition(state, sm.mark_unit_validating, (wrong,), False)
                validating = sm.mark_unit_validating(state, attempt, at=AT)
                self.assert_transition(validating, sm.record_candidate_ready, (candidate(validating, attempt=wrong),), False)
        for change in changes[:-1]:
            self.assert_transition(pending, sm.mark_unit_generating, (attempt.model_copy(update=change),), False)
        validating = sm.mark_unit_validating(state, attempt, at=AT)
        wrong = candidate(validating).model_copy(update={"input_fingerprint": "changed"})
        self.assert_transition(validating, sm.record_candidate_ready, (wrong,), False)

    def test_invalid_and_inflight_attempt_ids_cannot_be_reused(self):
        """模型序号即使匹配，平台 ID 也不能重用于另一 attempt 或另一 Unit。"""

        state = invalid(sm.begin_generation(run(unit(), unit("page:other")), at=AT))
        used = next(iter(state.candidates.values())).identity.attempt_id
        reused = identity(state).model_copy(update={"attempt_id": used})
        self.assert_transition(state, sm.mark_unit_generating, (reused,), False)
        state, active = start(state)
        reused = identity(state, "page:other").model_copy(update={"attempt_id": active.attempt_id})
        self.assert_transition(state, sm.mark_unit_generating, (reused,), False)

    def test_global_repair_validates_all_issues_and_targets_before_superseding(self):
        """空集合、混合致命问题、非法级别/类别或未知目标都不能启动部分 Global repair。"""

        state = phases()["validating"]
        valid = issue(level="global")
        cases = (
            (), (valid, issue(retryable=False)), (valid, issue()),
            (valid, issue(level="global", category="infrastructure")),
            (valid, issue("page:unknown", level="global")),
        )
        for issues in cases:
            with self.subTest(issues=issues):
                self.assert_transition(state, sm.begin_global_repair, (issues,), False)
        self.assertTrue(all(item.status == "valid" for item in state.candidates.values()))

    def test_local_invalid_never_retries_non_content_or_other_unit_issues(self):
        """系统、平台、输入、持久化失败以及错误目标不进入 Local 内容重试。"""

        state, _ = start(sm.begin_generation(run(), at=AT))
        cases = [(), (issue("page:other"),), (issue(level="global"),), (issue(retryable=False),)]
        cases.extend((issue(category=category),) for category in ("infrastructure", "platform", "input", "persistence"))
        for issues in cases:
            with self.subTest(issues=issues):
                bad = candidate(state, valid=False).model_copy(update={"validation_issues": issues})
                self.assert_transition(state, sm.record_candidate_invalid, (bad,), False)
        deterministic, _ = start(sm.begin_generation(run(unit(strategy="deterministic")), at=AT))
        self.assert_transition(deterministic, sm.record_candidate_invalid, (candidate(deterministic, valid=False),), False)
        self.assert_transition(state, sm.fail, (issue(),), False)

    def test_ready_requires_nonempty_locally_valid_candidate(self):
        """valid 标签不足以接纳空任务或仍有校验 Issue 的 Candidate。"""

        state, attempt = start(sm.begin_generation(run(), at=AT))
        state = sm.mark_unit_validating(state, attempt, at=AT)
        for change in ({"tasks": ()}, {"validation_issues": (issue(),)}):
            with self.subTest(change=change):
                self.assert_transition(state, sm.record_candidate_ready, (candidate(state).model_copy(update=change),), False)

    def test_scope_participation_and_snapshot_invariants_reject_invalid_inputs(self):
        """输入边界禁止错误参与方式、悬空候选、重复范围及非模型预算。"""

        for target, required in (("frontend:shell", "prerequisite_only"), ("application:root", "structural_only"), ("app:integration", "structural_only")):
            for strategy in ("model", "deterministic", "reuse_only"):
                with self.subTest(target=target, strategy=strategy), self.assertRaises(ValidationError):
                    unit(target, strategy)
            self.assertEqual(unit(target, required).generation_status, "not_required")
        for change in (
            {"status": "succeeded"}, {"global_repair_limit": 3}, {"global_repair_limit": 2.0}, {"planning_unit_ids": ()},
            {"required_unit_ids": (UNIT, UNIT)}, {"unit_states": {}}, {"status": "failed"},
        ):
            with self.subTest(run_change=change), self.assertRaises(ValidationError):
                run().model_copy(update=change)
        for change in (
            {"generation_status": "failed"}, {"generation_status": "candidate_ready"},
            {"generation_status": "generating"}, {"generation_status": "round_exhausted"},
            {"attempt_in_round": 4}, {"total_attempts": 1},
        ):
            with self.subTest(unit_change=change), self.assertRaises(ValidationError):
                unit().model_copy(update=change)
        with self.assertRaises(ValidationError):
            unit(strategy="deterministic").model_copy(update={"attempt_in_round": 1, "total_attempts": 1})
        with self.assertRaises(ValidationError):
            unit("frontend:auth-guard", "model")
        with self.assertRaises(ValidationError):
            phases()["global_check"].model_copy(update={"candidates": {}})
