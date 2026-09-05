"""T5.4 单写者提交、持久化失败及只读投影一致性测试。"""

import asyncio
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.services import planning_run as sm
from app.services.planning_frozen import plain_json
from app.services.planning_run_controller import (
    PlanningRunController, PlanningRunPersistenceError, PlanningRunProjectionError,
)
from app.services.planning_run_events import (
    AssemblyStarted, CandidateInvalid, CandidateReady, GenerationStarted,
    GlobalCheckStarted, GlobalRepairStarted, GlobalValidationStarted,
    PendingPersistenceStarted, RoundExhausted, RunCancelled, RunFailed,
    UnitAttemptStarted, UnitValidationStarted,
)
from app.workspace.planning_run_documents import load_planning_run, project_planning_run
from tests.planning_run_fixtures import AT, UNIT, candidate, identity, issue, run, unit


class PlanningRunControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """为每个 Controller 提供独立临时工作区及记录落盘顺序的发布器。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = {"workspace": directory.name}
        self.published = []
        self.controller = PlanningRunController(run(), self.workspace, publish=self.publish)

    async def publish(self, projection):
        """发布时验证轻量数据已完整落盘，随后保存不可变投影。"""

        self.assertEqual(load_planning_run(self.workspace), plain_json(projection))
        self.assertNotIn("candidates", projection)
        self.published.append(projection)

    async def make_ready(self, controller=None):
        """通过事件入口完整完成一次 Unit 生成及本地校验。"""

        controller = controller or self.controller
        attempt = identity(controller.snapshot)
        await controller.apply(UnitAttemptStarted(identity=attempt, at=AT))
        await controller.apply(UnitValidationStarted(identity=attempt, at=AT))
        return await controller.apply(CandidateReady(candidate=candidate(controller.snapshot), at=AT))

    async def test_sequential_revisions_and_all_normal_phase_events(self):
        """连续事件恰好逐次递增 revision，完成完整正常阶段链且不写 PendingPlan。"""

        self.assertIsNone(load_planning_run(self.workspace))
        await self.controller.apply(GenerationStarted(at=AT))
        await self.make_ready()
        for event in (GlobalCheckStarted, AssemblyStarted, GlobalValidationStarted, PendingPersistenceStarted):
            result = await self.controller.apply(event(at=AT))
            self.assertIs(result, self.controller.snapshot)
            self.assertEqual(project_planning_run(result), load_planning_run(self.workspace))
        self.assertEqual([snapshot["revision"] for snapshot in self.published], list(range(1, 9)))
        self.assertEqual(self.controller.snapshot.phase, "persisting_pending")
        self.assertEqual(self.controller.snapshot.unit_states[UNIT].total_attempts, 1)

    async def test_concurrent_submissions_wait_for_publication_without_lost_update(self):
        """两个 Unit 同时提交，后者必须等待前者发布完成，并读取最新状态计算 revision。"""

        initial = sm.begin_generation(run(unit(), unit("page:other")), at=AT)
        entered, release = asyncio.Event(), asyncio.Event()
        publication_order = []

        async def blocking_publish(projection):
            """阻塞第一份投影，主动让另一个 apply 竞争单写锁。"""

            publication_order.append(projection["revision"])
            await self.publish(projection)
            if len(publication_order) == 1:
                entered.set()
                await release.wait()

        controller = PlanningRunController(initial, self.workspace, publish=blocking_publish)
        first_event = UnitAttemptStarted(identity=identity(initial), at=AT)
        second_event = UnitAttemptStarted(identity=identity(initial, "page:other"), at=AT)
        first = asyncio.create_task(controller.apply(first_event))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(controller.apply(second_event))
        await asyncio.sleep(0)
        self.assertFalse(second.done())
        self.assertEqual(controller.snapshot.revision, 2)
        self.assertEqual(controller.snapshot.unit_states["page:other"].generation_status, "pending")
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 2)
        self.assertEqual([state.revision for state in results], [2, 3])
        self.assertEqual(publication_order, [2, 3])
        self.assertTrue(all(state.generation_status == "generating" for state in controller.snapshot.unit_states.values()))
        self.assertEqual(load_planning_run(self.workspace), project_planning_run(controller.snapshot))
        self.assertEqual(results[0].unit_states["page:other"].generation_status, "pending")

    async def test_illegal_transition_never_persists_or_publishes(self):
        """非法阶段和过期身份在写入前拒绝，不增加 revision、不发布快照。"""

        original = self.controller.snapshot
        with patch("app.services.planning_run_controller.write_planning_run_atomic") as writer:
            with self.assertRaises(sm.IllegalPlanningTransition):
                await self.controller.apply(UnitValidationStarted(identity=identity(original), at=AT))
            writer.assert_not_called()
        self.assertIs(self.controller.snapshot, original)
        self.assertEqual(self.published, [])
        self.assertIsNone(load_planning_run(self.workspace))

    async def test_persist_failure_preserves_committed_state_and_allows_explicit_resubmit(self):
        """真实原子替换失败不提交新版本；修复 IO 后显式重提同一事件只增加一次。"""

        await self.controller.apply(GenerationStarted(at=AT))
        previous = self.controller.snapshot
        persisted = load_planning_run(self.workspace)
        event = UnitAttemptStarted(identity=identity(previous), at=AT)
        with patch("app.workspace.json_documents.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(PlanningRunPersistenceError) as caught:
                await self.controller.apply(event)
        self.assertEqual(caught.exception.revision, 2)
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertIs(self.controller.snapshot, previous)
        self.assertEqual(load_planning_run(self.workspace), persisted)
        self.assertEqual(len(self.published), 1)
        result = await self.controller.apply(event)
        self.assertEqual(result.revision, 2)
        self.assertEqual(result.unit_states[UNIT].total_attempts, 1)
        self.assertEqual([snapshot["revision"] for snapshot in self.published], [1, 2])

    async def test_first_persist_failure_creates_no_success_snapshot(self):
        """首次落盘失败仍保留初态，文件和发布流均不冒充成功提交。"""

        with patch("app.workspace.json_documents.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(PlanningRunPersistenceError):
                await self.controller.apply(GenerationStarted(at=AT))
        self.assertEqual(self.controller.snapshot.revision, 0)
        self.assertEqual(self.controller.snapshot.phase, "preparing")
        self.assertEqual(self.published, [])
        self.assertIsNone(load_planning_run(self.workspace))

    async def test_snapshot_projection_and_disk_are_consistent_and_deeply_readonly(self):
        """领域快照不可变，发布投影和落盘一致且不泄漏 Candidate 正文。"""

        original = self.controller.snapshot
        await self.controller.apply(GenerationStarted(at=AT))
        ready = await self.make_ready()
        with self.assertRaises(ValidationError):
            ready.revision = 999
        with self.assertRaises(AttributeError):
            self.controller.snapshot = original
        with self.assertRaises(TypeError):
            ready.unit_states[UNIT] = unit()
        with self.assertRaises(TypeError):
            self.published[-1]["unit_states"][UNIT]["generation_status"] = "pending"
        self.assertEqual(self.controller.projection, self.published[-1])
        self.assertEqual(plain_json(self.controller.projection), load_planning_run(self.workspace))
        self.assertEqual(len(ready.candidates), 1)
        self.assertEqual(original.revision, 0)
        exported = ready.model_dump(mode="json")
        exported["unit_states"].clear()
        self.assertIn(UNIT, self.controller.snapshot.unit_states)

    async def test_invalid_round_exhaustion_and_global_repair_events(self):
        """全部 Local 事件及 Global reopen 都复用 T5.1，预算与 supersede 无额外修改。"""

        await self.controller.apply(GenerationStarted(at=AT))
        for _ in range(3):
            await self.controller.apply(UnitAttemptStarted(identity=identity(self.controller.snapshot), at=AT))
            await self.controller.apply(CandidateInvalid(candidate=candidate(self.controller.snapshot, valid=False), at=AT))
        await self.controller.apply(RoundExhausted(unit_id=UNIT, at=AT))
        await self.controller.apply(GlobalCheckStarted(at=AT))
        await self.controller.apply(GlobalRepairStarted(issues=(issue(level="global"),), at=AT))
        ready = await self.make_ready()
        old_id = ready.unit_states[UNIT].latest_candidate_id
        await self.controller.apply(GlobalCheckStarted(at=AT))
        reopened = await self.controller.apply(GlobalRepairStarted(issues=(issue(level="global"),), at=AT))
        self.assertEqual(reopened.candidates[old_id].status, "superseded")
        self.assertEqual(reopened.unit_states[UNIT].total_attempts, 4)
        self.assertEqual(reopened.global_repair_round, 2)
        self.assertEqual([item["revision"] for item in self.published], list(range(1, reopened.revision + 1)))

    async def test_fatal_and_cancel_events_reject_late_results_without_another_write(self):
        """终止事件落盘后，晚到 Worker 结果不能修改状态或产生新的发布。"""

        for event in (RunFailed(issue=issue(retryable=False), at=AT), RunCancelled(at=AT)):
            controller = PlanningRunController(run(), self.workspace, publish=self.publish)
            await controller.apply(GenerationStarted(at=AT))
            attempt = identity(controller.snapshot)
            await controller.apply(UnitAttemptStarted(identity=attempt, at=AT))
            terminal = await controller.apply(event)
            before = len(self.published)
            with self.assertRaises(sm.IllegalPlanningTransition):
                await controller.apply(UnitValidationStarted(identity=attempt, at=AT))
            self.assertEqual(len(self.published), before)
            self.assertEqual(load_planning_run(self.workspace), project_planning_run(terminal))

    async def test_publication_failure_reports_already_committed_revision(self):
        """通知失败不能回滚磁盘或重放转换，异常明确携带已提交快照。"""

        async def failing_publish(_projection):
            """模拟后续 AG-UI 适配器的发送异常。"""

            raise OSError("publication failed")

        controller = PlanningRunController(run(), self.workspace, publish=failing_publish)
        with self.assertRaises(PlanningRunProjectionError) as caught:
            await controller.apply(GenerationStarted(at=AT))
        self.assertIs(caught.exception.snapshot, controller.snapshot)
        self.assertEqual(controller.snapshot.revision, 1)
        self.assertEqual(load_planning_run(self.workspace), project_planning_run(controller.snapshot))
        with self.assertRaises(sm.IllegalPlanningTransition):
            await controller.apply(GenerationStarted(at=AT))

    async def test_event_boundary_rejects_mutation_unknown_events_and_disk_restore(self):
        """事件只有受限参数，无 revision/state patch；磁盘轻量投影不能恢复 Controller。"""

        event = GenerationStarted(at=AT)
        with self.assertRaises(ValidationError):
            event.at = "changed"
        with self.assertRaises(ValidationError):
            GenerationStarted(at=AT, revision=100)
        for unknown in ({"at": AT}, object()):
            with self.assertRaises(TypeError):
                await self.controller.apply(unknown)
        with self.assertRaises(TypeError):
            PlanningRunController(project_planning_run(run()), self.workspace)
        self.assertEqual(self.controller.snapshot.revision, 0)

    async def test_workspace_path_is_captured_before_caller_changes_context(self):
        """提交方修改原始 Graph context 不能将 Controller 的后续写入重定向。"""

        supplied = dict(self.workspace)
        controller = PlanningRunController(run(), supplied)
        supplied["workspace"] = "must-not-be-used"
        await controller.apply(GenerationStarted(at=AT))
        self.assertEqual(load_planning_run(self.workspace)["revision"], 1)

    async def test_publisher_cannot_reenter_and_deadlock_same_controller(self):
        """显式拒绝发布器直接重入，保留已经落盘的状态并报告发布错误。"""

        async def reentrant_publish(_projection):
            """模拟错误适配器在发布回调内再次提交事件。"""

            await controller.apply(RunCancelled(at=AT))

        controller = PlanningRunController(run(), self.workspace, publish=reentrant_publish)
        with self.assertRaises(PlanningRunProjectionError) as caught:
            await asyncio.wait_for(controller.apply(GenerationStarted(at=AT)), 2)
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        self.assertEqual(controller.snapshot.revision, 1)
