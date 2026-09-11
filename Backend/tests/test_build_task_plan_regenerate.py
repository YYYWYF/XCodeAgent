"""T7.5 PendingPlan → 全新串行 PlanningRun 的 Regenerate 生命周期测试。"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from app.services.dag_planning_regeneration import regenerate_pending_build_task_plan
from app.services.dag_planning_orchestrator import DagPlanningError, plan_dag_sequential
from app.services.planning_frozen import plain_json
from app.services.unit_generation import UnitGenerationInfrastructureError
from app.services.unit_generation_contracts import (
    UnitGenerationAttemptResult,
    UnitGenerationPolicy,
)
from app.workspace.planning_run_documents import load_planning_run
from app.workspace.task_documents import (
    load_pending_build_task_plan,
    write_build_task_plan_json,
    write_pending_build_task_plan_atomic,
)
from tests.dag_planning_baseline_fixtures import (
    confirmed_baseline,
    execution_scope,
    project_plan,
)
from tests.dag_planning_orchestrator_fixtures import model_tasks, shared_inputs
from tests.test_unit_generation_contracts import _policy_payload


class BuildTaskPlanRegenerateTests(unittest.IsolatedAsyncioTestCase):
    """验证旧 Pending 只被精确消费，后续运行不继承旧 Run 状态。"""

    def setUp(self) -> None:
        """创建当前 Formal、固定策略和隔离工作区。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = {"workspace": directory.name}
        self.formal = confirmed_baseline(project_plan(), execution_scope())
        self.formal_path = Path(write_build_task_plan_json(self.state, self.formal))
        self.policy = UnitGenerationPolicy(**_policy_payload())
        self.loaded_formals: list[dict | None] = []

    async def _generate(self, job, *, marker: str, **_: object) -> UnitGenerationAttemptResult:
        """返回带轮次标记的合法 Candidate，便于证明新 Run 未复用旧正文。"""

        tasks = model_tasks(job)
        for index, task in enumerate(tasks):
            task["id"] = f"{marker}:{job.identity.unit_id}:{index}"
            task["deliverables"][0]["id"] = f"{task['id']}:deliverable"
        return UnitGenerationAttemptResult(
            identity=job.identity,
            input_fingerprint=job.context.input_fingerprint,
            raw_response=json.dumps({"tasks": tasks}),
            tasks=tasks,
        )

    async def _write_old_pending(self) -> tuple[dict, dict]:
        """运行一次旧 PlanningRun 并写 Pending，返回 identity 与旧 Run 投影。"""

        old = await plan_dag_sequential(
            shared_inputs(self.formal),
            workspace_state=self.state,
            planning_run_id="planning-old",
            workflow_run_id="workflow-old",
            thread_id="thread-old",
            policy=self.policy,
            generate_once=lambda job, **kwargs: self._generate(job, marker="old", **kwargs),
        )
        run = old.planning_run
        write_pending_build_task_plan_atomic(
            self.state,
            plain_json(old.assembly.assembled_plan),
            owner_session_id="session-regenerate",
            planning_run_id=run.planning_run_id,
            workflow_run_id=run.workflow_run_id,
            base_confirmed_plan_digest=run.base_confirmed_plan_digest,
            input_fingerprint=run.input_fingerprint,
            build_execution_scope=plain_json(run.build_execution_scope),
            created_at=run.updated_at,
        )
        pending = load_pending_build_task_plan(self.state)
        return pending["draft_identity"], run

    def _current_inputs(self, formal: dict | None):
        """记录 lifecycle 传入的最新 Formal，并据此重建全部正式输入。"""

        self.loaded_formals.append(deepcopy(formal))
        return shared_inputs(formal)

    async def _regenerate(self, identity: dict, *, new_id: str = "planning-new"):
        """使用真实 T6.4 Orchestrator 执行一次 Regenerate。"""

        return await regenerate_pending_build_task_plan(
            self.state,
            planning_run_id=identity["planning_run_id"],
            draft_digest=identity["draft_digest"],
            workflow_run_id="workflow-new",
            thread_id="thread-new",
            current_inputs_factory=self._current_inputs,
            policy=self.policy,
            generate_once=lambda job, **kwargs: self._generate(job, marker="new", **kwargs),
            planning_run_id_factory=lambda: new_id,
        )

    async def test_normal_regenerate_reloads_current_formal_and_writes_new_pending(self) -> None:
        """精确旧身份应被消费，并从删除后重载的最新 Formal 生成新 Pending。"""

        identity, _ = await self._write_old_pending()
        current = deepcopy(self.formal)
        current["regenerate_baseline_marker"] = "fresh"
        write_build_task_plan_json(self.state, current)
        formal_bytes = self.formal_path.read_bytes()

        result = await self._regenerate(identity)

        pending = load_pending_build_task_plan(self.state)
        self.assertEqual(result.status, "regenerated")
        self.assertEqual(self.loaded_formals[0]["regenerate_baseline_marker"], "fresh")
        self.assertEqual(pending["draft_identity"]["planning_run_id"], "planning-new")
        self.assertEqual(pending["draft_identity"]["owner_session_id"], "session-regenerate")
        self.assertEqual(pending["draft_identity"]["workflow_run_id"], "workflow-new")
        self.assertEqual(self.formal_path.read_bytes(), formal_bytes)

    async def test_stale_regenerate_keeps_pending_and_does_not_start(self) -> None:
        """旧 digest 不能删除当前 Pending，也不能装载输入或创建 PlanningRun。"""

        identity, old_run = await self._write_old_pending()
        pending_path = Path(self.state["workspace"]) / ".xcodeagent/plans/build-task-plan.pending.json"
        pending_bytes = pending_path.read_bytes()
        factory = Mock(return_value="planning-should-not-start")

        result = await regenerate_pending_build_task_plan(
            self.state,
            planning_run_id=identity["planning_run_id"],
            draft_digest="f" * 64,
            workflow_run_id="workflow-new",
            thread_id="thread-new",
            current_inputs_factory=self._current_inputs,
            policy=self.policy,
            planning_run_id_factory=factory,
        )

        self.assertEqual(result.status, "stale_draft")
        self.assertEqual(self.loaded_formals, [])
        factory.assert_not_called()
        self.assertEqual(load_planning_run(self.state)["planning_run_id"], old_run.planning_run_id)
        self.assertEqual(pending_path.read_bytes(), pending_bytes)

    async def test_regenerate_allocates_different_planning_run_id(self) -> None:
        """Regenerate 必须拒绝复用旧 PlanningRun ID。"""

        identity, _ = await self._write_old_pending()

        with self.assertRaisesRegex(ValueError, "不同于旧草稿"):
            await self._regenerate(identity, new_id=identity["planning_run_id"])

        self.assertIsNone(load_pending_build_task_plan(self.state))

    async def test_old_candidate_and_retry_state_are_not_reused(self) -> None:
        """新 Run 只包含新 Candidate，且 Unit 轮次从初始预算重新开始。"""

        identity, old_run = await self._write_old_pending()
        old_candidate_ids = set(old_run.candidates)

        result = await self._regenerate(identity)

        pending = load_pending_build_task_plan(self.state)
        self.assertTrue(old_candidate_ids.isdisjoint(result.planning_run.candidates))
        self.assertFalse(any(task_id.startswith("old:") for task_id in pending["task_registry"]))
        self.assertTrue(any(task_id.startswith("new:") for task_id in pending["task_registry"]))
        self.assertTrue(all(unit.generation_round == 1 for unit in result.planning_run.unit_states.values()))

    async def test_generation_failure_does_not_restore_old_pending(self) -> None:
        """删除提交点后的新 Run 失败必须保持旧 Pending 已放弃。"""

        identity, _ = await self._write_old_pending()
        formal_bytes = self.formal_path.read_bytes()

        async def fail_new_run(job, **_: object) -> UnitGenerationAttemptResult:
            """在新 Run 首次模型调用中制造基础设施失败。"""

            raise UnitGenerationInfrastructureError(
                identity=job.identity,
                stage="model_invoke",
                cause=RuntimeError("new planning failed"),
            )

        with self.assertRaises(DagPlanningError):
            await regenerate_pending_build_task_plan(
                self.state,
                planning_run_id=identity["planning_run_id"],
                draft_digest=identity["draft_digest"],
                workflow_run_id="workflow-new",
                thread_id="thread-new",
                current_inputs_factory=self._current_inputs,
                policy=self.policy,
                generate_once=fail_new_run,
                planning_run_id_factory=lambda: "planning-new-failed",
            )

        self.assertIsNone(load_pending_build_task_plan(self.state))
        self.assertEqual(self.formal_path.read_bytes(), formal_bytes)
        failed_run = load_planning_run(self.state)
        self.assertEqual(failed_run["planning_run_id"], "planning-new-failed")
        self.assertEqual(failed_run["status"], "failed")


if __name__ == "__main__":
    unittest.main()
