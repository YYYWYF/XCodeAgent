"""DAG 中断证据落盘、精确来源复用及既有修复机制回归。"""

import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.build_task_planning_service import run_mainline_planning
from app.services.dag_planning_inputs import MainlinePlanningInputs
from app.services.frozen_contract_store import FrozenContractStore
from app.services.planning_run_controller import PlanningRunController
from app.services.planning_recovery_contracts import planning_recovery_snapshot_digest
from app.services.planning_run_events import (
    GenerationStarted, UnitAttemptStarted, UnitValidationStarted, CandidateReady,
    GlobalCheckStarted, GlobalRepairStarted,
)
from app.services.unit_generation_contracts import CandidateAttempt, UnitGenerationAttemptResult, UnitGenerationPolicy
from app.workspace.planning_interruption_documents import load_planning_interruption, planning_interruption_path
from tests.dag_planning_orchestrator_fixtures import planning_inputs, model_tasks
from tests.planning_run_fixtures import AT, identity, issue, repair_decision
from tests.test_unit_generation_contracts import _policy_payload


class PlanningInterruptionTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 Controller、文件和 Planning Service 验证中断后只生成缺失 Unit。"""

    def setUp(self):
        """隔离工作区，固定来源线程和 owner，避免读取用户运行状态。"""

        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = {"workspace": directory.name, "owner_session_id": "owner", "active_thread_id": "thread"}
        self.inputs = planning_inputs(required=["page:a", "page:b"])
        self.policy = UnitGenerationPolicy(**_policy_payload())

    async def partial_run(self):
        """保存 page:a 的有效正文并留下未开始的 page:b，模拟进程突然退出。"""

        requirements = self.inputs.requirements()
        run = self.inputs.create_run(requirements, planning_run_id="source-plan",
                                     workflow_run_id="source", thread_id="thread", at=AT)
        controller = PlanningRunController(run, self.state)
        await controller.apply(GenerationStarted(at=AT))
        attempt = identity(controller.snapshot, "page:a")
        await controller.apply(UnitAttemptStarted(identity=attempt, at=AT))
        await controller.apply(UnitValidationStarted(identity=attempt, at=AT))
        store = FrozenContractStore.create(planning_run_id=run.planning_run_id,
                                           formal_inputs=self.inputs.formal_contract_inputs)
        context = self.inputs.unit_context(run, requirements, "page:a", frozen_contract_store=store)
        tasks = model_tasks(SimpleNamespace(identity=attempt, context=context))
        candidate = CandidateAttempt.from_generated_attempt(
            candidate_id="candidate-" + "a" * 32, attempt=attempt,
            input_fingerprint=run.input_fingerprint, status="valid", tasks=tasks,
        )
        await controller.apply(CandidateReady(candidate=candidate, at=AT))
        return controller

    async def retry(self, inputs=None):
        """以新 execution 调用真实主线服务，并记录实际模型 Unit 调用。"""

        generated = []

        async def generate(job, **kwargs):
            """提供符合局部和全局规则的模型结果，不跳过既有验证器。"""

            generated.append(job.identity.unit_id)
            tasks = model_tasks(job)
            return UnitGenerationAttemptResult(identity=job.identity,
                input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}), tasks=tasks)

        current = MainlinePlanningInputs.model_validate({
            **(inputs or self.inputs).model_dump(mode="python"),
            "owner_session_id": "owner", "thread_id": "thread", "workflow_run_id": "child",
        })
        result = await run_mainline_planning(current, workspace_state=self.state,
            policy=self.policy, generate_once=generate,
            recovery_source_workflow_run_id="source", recovery_interrupted=True)
        return result, generated

    async def test_interruption_reuses_ready_sibling_and_keeps_confirmation(self):
        """中断读取已落盘候选，只生成缺失 Unit，最终仍停在 Pending 确认门。"""

        await self.partial_run()
        self.assertEqual(set(load_planning_interruption(self.state, "source").candidates_by_unit), {"page:a"})
        result, generated = await self.retry()
        self.assertEqual(generated, ["page:b"])
        run = result.validated_assembled_plan.planning_run
        restored = run.candidates[run.unit_states["page:a"].latest_candidate_id]
        self.assertEqual(restored.origin, "recovered")
        self.assertEqual(restored.recovered_from.source_planning_run_id, "source-plan")
        self.assertEqual(run.unit_states["page:a"].total_attempts, 0)
        self.assertEqual(result.terminal_status, "pending_confirmation")

    async def test_context_change_discards_candidates(self):
        """输入指纹变化时重新生成全部 Unit，不借旧候选绕过当前合同。"""

        await self.partial_run()
        inputs = self.inputs.model_copy(update={"workspace_snapshot": {"changed": True}})
        _, generated = await self.retry(inputs)
        self.assertEqual(set(generated), {"page:a", "page:b"})

    async def test_recovered_candidate_still_passes_local_validator(self):
        """来源和摘要合法也不能跳过 Local Validator，内容失效时重新生成。"""

        await self.partial_run()
        path = planning_interruption_path(self.state, "source")
        payload = json.loads(path.read_text())
        payload["candidates_by_unit"]["page:a"]["tasks"][0]["owner"] = "unknown-owner"
        payload["snapshot_digest"] = planning_recovery_snapshot_digest(payload)
        path.write_text(json.dumps(payload))
        self.assertIsNotNone(load_planning_interruption(self.state, "source"))
        _, generated = await self.retry()
        self.assertEqual(set(generated), {"page:a", "page:b"})

    async def test_wrong_owner_thread_and_tampering_are_rejected(self):
        """不同会话、线程和篡改正文不能读取候选；损坏只放弃复用。"""

        await self.partial_run()
        for key in ("owner_session_id", "active_thread_id"):
            self.assertIsNone(load_planning_interruption({**self.state, key: "other"}, "source"))
        path = planning_interruption_path(self.state, "source")
        payload = json.loads(path.read_text())
        payload["source_revision"] += 1
        path.write_text(json.dumps(payload))
        self.assertIsNone(load_planning_interruption(self.state, "source"))
        _, generated = await self.retry()
        self.assertEqual(set(generated), {"page:a", "page:b"})

    async def test_global_repair_invalidates_saved_candidate(self):
        """全局修复重开 Unit 后旧 ready 候选不能从磁盘复活。"""

        self.inputs = planning_inputs(required=["page:a"])
        controller = await self.partial_run()
        await controller.apply(GlobalCheckStarted(at=AT))
        await controller.apply(GlobalRepairStarted(decision=repair_decision(issue("page:a", level="global")), at=AT))
        snapshot = load_planning_interruption(self.state, "source")
        self.assertEqual(dict(snapshot.candidates_by_unit), {})
        self.assertEqual(controller.snapshot.unit_states["page:a"].generation_round, 2)

    async def test_snapshot_write_failure_does_not_fail_planning(self):
        """优化落盘失败不替换正常规划结果或吞掉修复机制的错误。"""

        with patch("app.workspace.planning_interruption_documents.write_json_atomic", side_effect=OSError("disk full")):
            result, generated = await self.retry()
        self.assertEqual(result.terminal_status, "pending_confirmation")
        self.assertEqual(set(generated), {"page:a", "page:b"})

    async def test_stale_snapshot_after_write_failure_is_not_reused(self):
        """新版本证据写入失败时旧快照版本不匹配，不能复活已过期指针。"""

        controller = await self.partial_run()
        with patch("app.workspace.planning_interruption_documents.write_json_atomic", side_effect=OSError("disk full")):
            await controller.apply(UnitAttemptStarted(identity=identity(controller.snapshot, "page:b"), at=AT))
        self.assertIsNone(load_planning_interruption(self.state, "source"))
