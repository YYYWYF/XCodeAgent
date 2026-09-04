"""T6.3 真实 Controller/Local 编排与 mock Global Validator 的串行循环回归。"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.services import planning_run as sm
from app.services.global_repair_orchestrator import run_global_repair_loop
from app.services.planning_run_controller import PlanningRunController
from app.services.unit_generation import UnitGenerationInfrastructureError
from app.services.unit_generation_contracts import UnitGenerationAttemptResult, UnitGenerationPolicy
from app.services.unit_generation_orchestrator import run_unit_generation_round
from app.workspace.planning_run_documents import load_planning_run
from tests.planning_run_fixtures import AT, UNIT, exhausted, issue, ready, run, unit
from tests.test_global_repair_controller import attribute, global_issue
from tests.test_unit_candidate_validator import _context, _task
from tests.test_unit_generation_contracts import _policy_payload


SECOND = "frontend:api-client"
THIRD = "frontend:api:orders-api"


class SequentialGlobalRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """初始化隔离工作区、调用记录及可控模型失败配置。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = {"workspace": directory.name}
        self.policy = UnitGenerationPolicy(**_policy_payload())
        self.attempts = []
        self.checks = []
        self.dispatches = []
        self.fail_attempts = {}
        self.infrastructure_failure = False
        self.blocker = None
        self.active_generations = 0
        self.max_active_generations = 0

    def _prepare(self, *unit_ids, exhausted_ids=()):
        """创建所有 Unit 本轮已结束的快照，保留完整初始候选供前后比较。"""

        initial = sm.begin_generation(run(*(unit(key) for key in unit_ids or (UNIT,))), at=AT)
        for key in initial.planning_unit_ids:
            initial = exhausted(initial, key) if key in exhausted_ids else ready(initial, key)
        self.initial = initial
        self.controller = PlanningRunController(initial, self.workspace)

    async def _generate(self, job, **feedback):
        """模拟一次模型输出，Local Validator、计数、Candidate 接纳均使用生产实现。"""

        self.attempts.append((job.identity, feedback, self.controller.snapshot))
        self.active_generations += 1
        self.max_active_generations = max(self.max_active_generations, self.active_generations)
        try:
            await asyncio.sleep(0)
            if self.infrastructure_failure:
                raise UnitGenerationInfrastructureError(
                    identity=job.identity, stage="model_invoke", cause=ConnectionError("offline"),
                )
            task = _task(job.identity.unit_id, task_id=f"task-{job.identity.attempt_id}")
            if job.identity.attempt_in_round <= self.fail_attempts.get(
                (job.identity.unit_id, job.identity.generation_round), 0,
            ):
                task["owner"] = "backend"
            return UnitGenerationAttemptResult(
                identity=job.identity, input_fingerprint=job.context.input_fingerprint,
                raw_response="mock model", tasks=(task,),
            )
        finally:
            self.active_generations -= 1

    async def _regenerate(self, current):
        """把受影响 Unit 的冻结输入与本轮 Global 反馈接到真实 T6.1 Local 编排。"""

        self.dispatches.append(current)
        snapshot = self.controller.snapshot
        context = _context(current.unit_id).model_copy(update={
            "planning_run_id": snapshot.planning_run_id,
            "input_fingerprint": snapshot.input_fingerprint,
            "base_confirmed_plan_digest": snapshot.base_confirmed_plan_digest,
            "build_execution_scope": snapshot.build_execution_scope,
        })
        await run_unit_generation_round(
            self.controller, context, self.policy, global_feedback=current.current_issues,
            generate_once=self._generate, now=lambda: AT,
        )

    async def _regenerate_round(self, units):
        """按旧串行夹具执行完整目标批次，生产接入另用并发 Scheduler。"""

        for current in units:
            await self._regenerate(current)

    async def _loop(self, *batches):
        """按 cycle 提供完整规则 Issues，真实 T4.1 归因后交给 Global 循环。"""

        async def validate(snapshot):
            """收集整批问题，包含重复目标及 blocker，绝不提前过滤可重试子集。"""

            index = len(self.checks)
            self.checks.append(snapshot)
            targets = batches[index] if index < len(batches) else ()
            issues = [global_issue(snapshot, key).model_copy(update={
                "code": ("GLOBAL_CONTRACT_VIOLATION", "GLOBAL_CROSS_UNIT_DEPENDENCY_INVALID",
                         "GLOBAL_DEPENDENCY_CYCLE")[number % 3],
                "details": {"rule_index": number},
            }) for number, key in enumerate(targets)]
            if self.blocker:
                issues.append(self.blocker)
            return attribute(snapshot, issues)

        return await run_global_repair_loop(
            self.controller, validate_global=validate,
            regenerate_round=self._regenerate_round, now=lambda: AT,
        )

    async def test_initial_global_success_spends_no_budget_or_pending(self):
        """初次检查无问题时不重开，成功边界也不写 Pending 或正式计划。"""

        self._prepare()
        result = await self._loop()
        state = self.controller.snapshot
        self.assertEqual(result.issues, ())
        self.assertEqual((state.status, state.phase, state.global_repair_round), ("active", "global_check", 0))
        self.assertEqual(state.candidates, self.initial.candidates)
        self.assertEqual(self.dispatches, [])
        plans = Path(self.workspace["workspace"]) / ".xcodeagent" / "plans"
        self.assertEqual({path.name for path in plans.iterdir()}, {"planning-run.json"})

    async def test_one_affected_reopens_and_rechecks(self):
        """一个受影响 Unit 只重生一次，随后用当前新 Candidate 再做 Global 检查。"""

        self._prepare()
        result = await self._loop((UNIT,))
        state = self.controller.snapshot
        current = state.unit_states[UNIT]
        self.assertEqual(result.issues, ())
        self.assertEqual([check.global_repair_round for check in self.checks], [0, 1])
        self.assertEqual((current.generation_round, current.attempt_in_round, current.total_attempts), (2, 1, 2))
        self.assertEqual([item.unit_id for item in self.dispatches], [UNIT])
        self.assertNotEqual(current.latest_candidate_id, self.initial.unit_states[UNIT].latest_candidate_id)
        self.assertEqual(load_planning_run(self.workspace)["global_repair_round"], 1)

    async def test_two_affected_share_one_round_and_run_sequentially(self):
        """两目标先一起 supersede，再顺序完成各自 Local；整批只消耗一轮 G。"""

        self._prepare(UNIT, SECOND)
        await self._loop((UNIT, SECOND))
        self.assertEqual(self.controller.snapshot.global_repair_round, 1)
        self.assertEqual([item.unit_id for item in self.dispatches], sorted((UNIT, SECOND)))
        self.assertEqual(self.max_active_generations, 1)
        first_snapshot = self.attempts[0][2]
        for key in (UNIT, SECOND):
            old_id = self.initial.unit_states[key].latest_candidate_id
            self.assertEqual(first_snapshot.candidates[old_id].status, "superseded")
            self.assertIsNone(first_snapshot.unit_states[key].latest_candidate_id)
        self.assertEqual(self.attempts[1][2].unit_states[SECOND].generation_status, "candidate_ready")

    async def test_same_unit_multiple_issues_reopens_once_with_all_feedback(self):
        """同 Unit 多个不同问题只重开一次，全部反馈进入这次 Local 编排。"""

        self._prepare()
        await self._loop((UNIT, UNIT, UNIT))
        self.assertEqual(len(self.dispatches), 1)
        self.assertEqual(len(self.attempts[0][1]["global_feedback"]), 3)
        self.assertEqual(self.controller.snapshot.global_repair_round, 1)
        self.assertEqual(len(self.controller.snapshot.unit_states[UNIT].round_history), 1)

    async def test_unaffected_preserved_across_different_repair_targets(self):
        """两轮分别修复不同目标，第三个 Unit 状态和 Candidate 全文始终原样保留。"""

        self._prepare(UNIT, SECOND, THIRD)
        await self._loop((UNIT,), (SECOND,))
        state = self.controller.snapshot
        original = self.initial.unit_states[THIRD]
        self.assertEqual(state.unit_states[THIRD], original)
        self.assertEqual(state.candidates[original.latest_candidate_id], self.initial.candidates[original.latest_candidate_id])
        self.assertEqual([item.unit_id for item in self.dispatches], [UNIT, SECOND])
        self.assertEqual(state.global_repair_round, 2)
        self.assertEqual(state.unit_states[UNIT].generation_round, 2)
        self.assertEqual(state.unit_states[SECOND].generation_round, 2)

    async def test_old_candidate_superseded_without_changing_body(self):
        """旧有效 Candidate 在生成前即失去当前资格，正文、身份和旧快照保持不变。"""

        self._prepare()
        old_id = self.initial.unit_states[UNIT].latest_candidate_id
        before = self.initial.candidates[old_id]
        await self._loop((UNIT,))
        old = self.controller.snapshot.candidates[old_id]
        self.assertEqual(old, before.model_copy(update={"status": "superseded"}))
        self.assertEqual(before.status, "valid")
        self.assertIsNone(self.dispatches[0].latest_candidate_id)
        self.assertEqual(self.dispatches[0].attempt_in_round, 0)

    async def test_local_budget_resets_each_round_and_feedback_does_not_leak(self):
        """每轮重新允许三次 Local，Global 反馈跨 Attempt 保持而 Local 从空开始。"""

        self._prepare()
        self.fail_attempts = {(UNIT, 2): 2, (UNIT, 3): 2}
        await self._loop((UNIT,), (UNIT,))
        current = self.controller.snapshot.unit_states[UNIT]
        self.assertEqual([(item[0].generation_round, item[0].attempt_in_round) for item in self.attempts],
                         [(2, 1), (2, 2), (2, 3), (3, 1), (3, 2), (3, 3)])
        self.assertEqual(current.total_attempts, 7)
        for _, feedback, _ in (self.attempts[0], self.attempts[3]):
            self.assertEqual(feedback["local_feedback"], ())
            self.assertEqual(len(feedback["global_feedback"]), 1)
        self.assertTrue(self.attempts[1][1]["local_feedback"])

    async def test_repair_failure_never_restores_old_candidate(self):
        """两次 repair 均耗尽时当前指针为空，旧 Candidate 永远不能用于 completeness。"""

        self._prepare()
        old_id = self.initial.unit_states[UNIT].latest_candidate_id
        self.fail_attempts = {(UNIT, 2): 3, (UNIT, 3): 3}
        result = await self._loop((UNIT,))
        state = self.controller.snapshot
        self.assertEqual(state.status, "failed")
        self.assertEqual(state.failure.code, "GLOBAL_REPAIR_LIMIT_EXHAUSTED")
        self.assertEqual(state.candidates[old_id].status, "superseded")
        self.assertIsNone(state.unit_states[UNIT].latest_candidate_id)
        self.assertEqual(state.unit_states[UNIT].generation_status, "round_exhausted")
        self.assertEqual(result.issues[0].code, "GLOBAL_CANDIDATE_MISSING")
        self.assertEqual(len(self.checks), 1)
        self.assertEqual(len(self.attempts), 6)

    async def test_repair_limit_exhausted_never_starts_third_repair(self):
        """三次 Global 检查都报错时只启动两次 repair，最后一批诊断被持久化。"""

        self._prepare()
        result = await self._loop((UNIT,), (UNIT,), (UNIT,))
        state = self.controller.snapshot
        self.assertEqual([check.global_repair_round for check in self.checks], [0, 1, 2])
        self.assertEqual((state.status, state.global_repair_round), ("failed", 2))
        self.assertEqual(len(self.dispatches), 2)
        self.assertEqual(state.unit_states[UNIT].generation_round, 3)
        self.assertEqual(load_planning_run(self.workspace)["failure"]["details"]["issues"],
                         [item.model_dump(mode="json") for item in result.issues])

    async def test_initial_missing_candidates_aggregate_before_repair(self):
        """初轮两 Unit 耗尽只消耗一轮 Global，齐全前不把部分 Candidate 送入 Validator。"""

        self._prepare(UNIT, SECOND, THIRD, exhausted_ids=(UNIT, SECOND))
        await self._loop()
        self.assertEqual([check.global_repair_round for check in self.checks], [1])
        self.assertEqual([item.unit_id for item in self.dispatches], sorted((UNIT, SECOND)))
        self.assertEqual(self.controller.snapshot.unit_states[UNIT].total_attempts, 4)
        self.assertEqual(self.controller.snapshot.unit_states[THIRD], self.initial.unit_states[THIRD])

    async def test_mixed_blocker_fails_whole_batch_without_superseding(self):
        """完整决策中一个 blocker 阻断全部目标，不能先修复可重试的子问题。"""

        self._prepare(UNIT, SECOND)
        self.blocker = issue(level="global", category="platform", retryable=False)
        result = await self._loop((UNIT, SECOND))
        state = self.controller.snapshot
        self.assertFalse(result.retryable)
        self.assertEqual(state.failure.code, "GLOBAL_REPAIR_BLOCKED")
        self.assertEqual(state.global_repair_round, 0)
        self.assertEqual(state.candidates, self.initial.candidates)
        self.assertEqual(len(state.failure.details["issues"]), 3)
        self.assertEqual(self.dispatches, [])

    async def test_all_rounds_exhausted_caps_total_attempts_at_nine(self):
        """初轮及两次 repair 均三次失败时总预算精确为九，不进入任何完整 DAG 检查。"""

        self._prepare(exhausted_ids=(UNIT,))
        self.fail_attempts = {(UNIT, 2): 3, (UNIT, 3): 3}
        await self._loop()
        state = self.controller.snapshot
        self.assertEqual((state.status, state.global_repair_round), ("failed", 2))
        self.assertEqual(state.unit_states[UNIT].total_attempts, 9)
        self.assertEqual(len(state.candidates), 9)
        self.assertEqual(self.checks, [])

    async def test_infrastructure_failure_stops_remaining_affected_units(self):
        """第一受影响 Unit 传输失败立即终止 Run，另一 Unit 不生成且旧候选不恢复。"""

        self._prepare(UNIT, SECOND)
        self.infrastructure_failure = True
        with self.assertRaises(UnitGenerationInfrastructureError):
            await self._loop((UNIT, SECOND))
        state = self.controller.snapshot
        self.assertEqual((state.status, state.global_repair_round), ("failed", 1))
        self.assertEqual(state.failure.category, "infrastructure")
        self.assertEqual(len(self.attempts), 1)
        for key in (UNIT, SECOND):
            self.assertEqual(state.unit_states[key].generation_status, "aborted")
            self.assertIsNone(state.unit_states[key].latest_candidate_id)
            self.assertEqual(state.candidates[self.initial.unit_states[key].latest_candidate_id].status, "superseded")
