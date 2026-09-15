"""T9.5 Workflow cancellation 向 Unit Scheduler 传播的集成回归。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest

from app.protocols.workflow.run_control import WorkflowRunRegistry
from app.services.dag_planning_orchestrator import plan_dag_sequential
from app.services.planning_frozen import plain_json
from app.services.unit_generation_contracts import (
    UnitGenerationAttemptResult,
    UnitGenerationPolicy,
)
from app.workspace.planning_run_documents import load_planning_run
from tests.dag_planning_orchestrator_fixtures import model_tasks, planning_inputs
from tests.planning_run_fixtures import AT
from tests.test_unit_generation_contracts import _policy_payload
from tests.test_unit_generation_orchestrator import _settings


def _five_page_inputs():
    """构造五个 model Unit，用于稳定形成 active=3、queued=2。"""

    base = planning_inputs()
    plan = plain_json(base.project_plan)
    for page_id in ("d", "e"):
        plan["pages"].append({"pageId": page_id, "path": f"/{page_id}"})
        plan["page_implementation_contracts"].append({
            "schema_version": "page-implementation-contract.v1",
            "pageId": page_id,
            "uiDesignRef": {
                "path": f".xcodeagent/ui-design/pages/{page_id.upper()}/index.tsx",
            },
            "requiredEndpointIds": [],
        })
    return planning_inputs(
        plan=plan,
        required=["frontend:shell", *(f"page:{page_id}" for page_id in "abcde")],
    )


def _success(job) -> UnitGenerationAttemptResult:
    """为指定 Attempt 构造通过真实 Local Validator 的完整结果。"""

    return UnitGenerationAttemptResult(
        identity=job.identity,
        input_fingerprint=job.context.input_fingerprint,
        raw_response="mock success",
        tasks=tuple(model_tasks(job)),
    )


class UnitGenerationSchedulerCancellationTests(unittest.IsolatedAsyncioTestCase):
    """验证 Workflow task.cancel 不会留下队列、worker 或伪正常返回。"""

    def setUp(self) -> None:
        """创建隔离工作区、固定策略和进度记录。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = {"workspace": directory.name}
        self.policy = UnitGenerationPolicy(**_policy_payload())
        self.projections: list[dict] = []

    async def _publish(self, projection) -> None:
        """保留 Controller 轻量投影，以断言取消后没有 Global progression。"""

        self.projections.append(plain_json(projection))

    async def test_cancel_active_three_with_two_queued_persists_and_propagates(self) -> None:
        """Cancel 停止两个 queued Job，取消三个 active worker 并拒收晚到成功。"""

        active_started = asyncio.Event()
        never = asyncio.Event()
        starts: list[str] = []
        active_cancellations: list[str] = []
        late_success_returned = asyncio.Event()

        async def generate(job, **_kwargs):
            """让首批三个 worker 阻塞，A 吞掉取消并返回晚到成功。"""

            unit_id = job.identity.unit_id
            starts.append(unit_id)
            if len(starts) == 3:
                active_started.set()
            try:
                await never.wait()
            except asyncio.CancelledError:
                active_cancellations.append(unit_id)
                if unit_id == "page:a":
                    late_success_returned.set()
                    return _success(job)
                raise

        planning_task = asyncio.create_task(plan_dag_sequential(
            _five_page_inputs(),
            workspace_state=self.workspace,
            planning_run_id="cancelled-planning-run",
            workflow_run_id="workflow-active",
            thread_id="thread-cancel",
            policy=self.policy,
            settings=_settings(),
            generate_once=generate,
            publish=self._publish,
            now=lambda: AT,
        ))
        registry = WorkflowRunRegistry()
        registry.register(
            "workflow-active",
            planning_task,
            workspace=self.workspace["workspace"],
        )
        await asyncio.wait_for(active_started.wait(), 1)

        self.assertTrue(registry.cancel("workflow-active"))
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(planning_task, 1)
        registry.unregister("workflow-active", planning_task)

        self.assertTrue(planning_task.cancelled())
        self.assertEqual(starts, ["page:a", "page:b", "page:c"])
        self.assertCountEqual(
            active_cancellations,
            ["page:a", "page:b", "page:c"],
        )
        self.assertTrue(late_success_returned.is_set())
        persisted = load_planning_run(self.workspace)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted["status"], "cancelled")
        self.assertEqual(persisted["phase"], "generating_units")
        self.assertEqual(persisted["revision"], 5)
        self.assertTrue(all(
            unit["generation_status"] == "aborted"
            and unit["expected_identity"] is None
            for unit in persisted["unit_states"].values()
            if unit["generation_strategy"] == "model"
        ))
        self.assertEqual(
            [persisted["unit_states"][f"page:{key}"]["total_attempts"] for key in "abcde"],
            [1, 1, 1, 0, 0],
        )
        self.assertFalse(any(
            projection["phase"] in {"global_check", "assembling", "validating"}
            for projection in self.projections
        ))

    async def test_cancel_during_assembly_stops_remaining_global_progression(self) -> None:
        """Scheduler 完成后取消仍由 orchestrator 持久化，且不进入 validating。"""

        assembly_started = asyncio.Event()
        release_publication = asyncio.Event()

        async def publish(projection) -> None:
            """在 assembling 已落盘但未发布完成时暴露取消窗口。"""

            snapshot = plain_json(projection)
            self.projections.append(snapshot)
            if snapshot["phase"] == "assembling":
                assembly_started.set()
                await release_publication.wait()

        async def generate(job, **_kwargs):
            """让正常 generation round 快速到达 Assembly 边界。"""

            return _success(job)

        planning_task = asyncio.create_task(plan_dag_sequential(
            planning_inputs(),
            workspace_state=self.workspace,
            planning_run_id="assembly-cancelled-run",
            workflow_run_id="workflow-assembly",
            thread_id="thread-assembly",
            policy=self.policy,
            settings=_settings(),
            generate_once=generate,
            publish=publish,
            now=lambda: AT,
        ))
        await asyncio.wait_for(assembly_started.wait(), 1)
        planning_task.cancel()
        release_publication.set()

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(planning_task, 1)

        persisted = load_planning_run(self.workspace)
        self.assertIsNotNone(persisted)
        self.assertEqual((persisted["status"], persisted["phase"]), ("cancelled", "assembling"))
        self.assertFalse(any(
            projection["phase"] == "validating"
            for projection in self.projections
        ))


if __name__ == "__main__":
    unittest.main()
