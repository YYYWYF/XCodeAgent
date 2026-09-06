"""T4.1 完整 GlobalRepairDecision 穿过 T5 事件、Controller 和纯状态边界的集成验证。"""

import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.services import planning_run as sm
from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.global_issue_attribution import GlobalRepairDecision, attribute_global_issues
from app.services.global_planning_validation import CandidateOwnership, TaskProvenance
from app.services.planning_run_controller import PlanningRunController
from app.services.planning_run_events import GlobalRepairStarted
from app.workspace.planning_run_documents import load_planning_run, write_planning_run_atomic
from tests.planning_run_fixtures import AT, UNIT, exhausted, issue, ready, run, unit


def global_issue(state, unit_id=UNIT):
    """模拟确定性合同规则对真实当前 Candidate 的明确责任声明。"""

    current = state.candidates[state.unit_states[unit_id].latest_candidate_id]
    return issue(unit_id, level="global").model_copy(update={
        "code": "GLOBAL_CONTRACT_VIOLATION", "task_ids": tuple(task["id"] for task in current.tasks),
    })


def attribute(state, issues):
    """从真实内存 Run 构造 T4.1 输入，完整性门禁和聚合均调用生产入口。"""

    owners = [CandidateOwnership.from_candidate(state.candidates[item.latest_candidate_id])
              for item in state.unit_states.values() if item.latest_candidate_id]
    provenance = [TaskProvenance(
        task_id=task, unit_id=owner.unit_id, source="candidate", candidate_id=owner.candidate_id,
    ) for owner in owners for task in owner.task_ids]
    return attribute_global_issues(
        issues, planning_unit_ids=state.planning_unit_ids, candidate_ownership=owners,
        task_provenance=provenance,
        reuse_facts=ReuseFacts(retained_task_ids_by_unit={}, reusable_capabilities_by_unit={},
                              retained_endpoint_owners=(), external_capabilities=()),
    )


class GlobalRepairControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """准备一个已经完成 Global Barrier 的真实 Run 和独立持久化目录。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = {"workspace": directory.name}
        self.state = sm.begin_global_check(ready(sm.begin_generation(run(), at=AT)), at=AT)

    async def assert_blocked(self, state, decision):
        """被阻断决策不得消耗预算、supersede、写盘或发布，原证据完整保留。"""

        publisher = AsyncMock()
        controller = PlanningRunController(state, self.workspace, publish=publisher)
        write_planning_run_atomic(self.workspace, state)
        previous = load_planning_run(self.workspace)
        encoded = state.model_dump_json()
        with patch("app.services.planning_run_controller.write_planning_run_atomic") as writer:
            with self.assertRaises(sm.IllegalPlanningTransition):
                await controller.apply(GlobalRepairStarted(decision=decision, at=AT))
            writer.assert_not_called()
        publisher.assert_not_awaited()
        self.assertEqual(controller.snapshot.model_dump_json(), encoded)
        self.assertEqual(load_planning_run(self.workspace), previous)

    async def test_retryable_issue_with_platform_or_nonretryable_blocker_cannot_repair(self):
        """真实 T4.1 决策内即使保留可修复子问题，总 blocker 仍阻止整个 repair。"""

        for blocker in (
            issue(level="global", category="platform", retryable=False),
            issue(level="global", category="generation", retryable=False),
        ):
            with self.subTest(category=blocker.category):
                decision = attribute(self.state, (global_issue(self.state), blocker))
                self.assertFalse(decision.retryable)
                self.assertEqual(decision.retry_unit_ids, ())
                self.assertTrue(any(item.retryable for item in decision.issues))
                await self.assert_blocked(self.state, decision)

    async def test_missing_candidate_blocker_prevents_repair_of_ready_subset(self):
        """另一个 Unit 耗尽且无 Candidate 时，不能只重开现有 ready Candidate 来绕过完整性门禁。"""

        state = sm.begin_generation(run(unit(), unit("page:missing")), at=AT)
        state = ready(state)
        state = exhausted(state, "page:missing")
        state = sm.begin_global_check(state, at=AT)
        decision = attribute(state, (global_issue(state),))
        self.assertIn("GLOBAL_CANDIDATE_MISSING", [item.code for item in decision.issues])
        await self.assert_blocked(state, decision)

    async def test_empty_decision_does_not_consume_global_round(self):
        """全局无 Issue 时不是 repair 请求，不能空耗额度。"""

        await self.assert_blocked(self.state, attribute(self.state, ()))

    async def test_unblocked_full_decision_reopens_all_targets_once(self):
        """T4.1 排序去重的全部目标一起重开，未归因 Unit 和旧 Candidate 正文保持不变。"""

        targets = (UNIT, "page:customers", "page:unaffected")
        state = sm.begin_generation(run(*(unit(target) for target in targets)), at=AT)
        for target in targets:
            state = ready(state, target)
        state = sm.begin_global_check(state, at=AT)
        first = global_issue(state)
        decision = attribute(state, (first, global_issue(state, targets[1]), first))
        publisher = AsyncMock()
        controller = PlanningRunController(state, self.workspace, publish=publisher)
        result = await controller.apply(GlobalRepairStarted(decision=decision, at=AT))
        self.assertEqual(decision.retry_unit_ids, tuple(sorted(targets[:2])))
        self.assertEqual((result.revision, result.global_repair_round), (state.revision + 1, 1))
        self.assertEqual(result.global_issues, decision.issues)
        for target in decision.retry_unit_ids:
            old_id = state.unit_states[target].latest_candidate_id
            self.assertEqual(result.candidates[old_id].status, "superseded")
            self.assertEqual(result.candidates[old_id].tasks, state.candidates[old_id].tasks)
            self.assertEqual(result.unit_states[target].generation_round, 2)
        self.assertEqual(result.unit_states[targets[2]], state.unit_states[targets[2]])
        publisher.assert_awaited_once()
        self.assertEqual(load_planning_run(self.workspace)["revision"], result.revision)

    async def test_raw_issue_subset_cannot_enter_event_or_pure_transition(self):
        """旧裸 issues 入口全部关闭；不能从 blocked 决策筛出 retryable 子集交给 T5。"""

        decision = attribute(self.state, (global_issue(self.state), issue(level="global", retryable=False)))
        subset = tuple(item for item in decision.issues if item.retryable)
        self.assertTrue(subset)
        with self.assertRaises(ValidationError):
            GlobalRepairStarted(issues=subset, at=AT)
        with self.assertRaises(ValidationError):
            GlobalRepairStarted(decision=decision, issues=subset, at=AT)
        with self.assertRaises(sm.IllegalPlanningTransition):
            sm.begin_global_repair(self.state, subset, at=AT)

    async def test_unvalidated_or_inconsistent_decision_is_revalidated_at_both_boundaries(self):
        """model_construct 伪造总开关/目标不能绕过 Event 或纯状态入口的完整契约校验。"""

        decision = attribute(self.state, (global_issue(self.state), issue(level="global", retryable=False)))
        forged = GlobalRepairDecision.model_construct(retryable=True, retry_unit_ids=(UNIT,), issues=decision.issues)
        with self.assertRaises(ValidationError):
            GlobalRepairStarted(decision=forged, at=AT)
        with self.assertRaises(ValidationError):
            sm.begin_global_repair(self.state, forged, at=AT)
        valid = attribute(self.state, (global_issue(self.state),))
        for update in ({"retryable": False}, {"retry_unit_ids": ()}, {"retry_unit_ids": ("page:other",)}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                valid.model_copy(update=update)
