"""T11.6.2 Mainline Planning orchestration service 集成测试。"""

from __future__ import annotations

import asyncio
from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.services.build_task_planning_service import run_mainline_planning
from app.services.dag_planning_inputs import MainlinePlanningInputs
from app.services.dag_planning_orchestrator import DagPlanningError
from app.services.planning_frozen import plain_json
from app.services.unit_generation import UnitGenerationInfrastructureError
from app.services.unit_generation_contracts import (
    UnitGenerationAttemptResult,
    UnitGenerationPolicy,
)
from app.workspace.planning_run_documents import load_planning_run
from app.workspace.task_documents import (
    build_task_plan_json_path,
    load_pending_build_task_plan,
    validate_pending_self_digest,
    write_build_task_plan_json,
    write_pending_build_task_plan_atomic,
)
from tests.dag_planning_baseline_fixtures import (
    confirmed_baseline,
    execution_scope,
    project_plan,
)
from tests.dag_planning_orchestrator_fixtures import (
    model_tasks,
    planning_inputs,
    shared_inputs,
)
from tests.test_unit_generation_contracts import _policy_payload


class BuildTaskPlanningServiceTests(unittest.IsolatedAsyncioTestCase):
    """验证 mainline service 独占 Run lifecycle 并在成功后写 Pending。"""

    def setUp(self) -> None:
        """创建隔离工作区、固定策略和 Unit 调用记录。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = {"workspace": directory.name}
        self.policy = UnitGenerationPolicy(**_policy_payload())
        self.calls = []

    def _mainline_inputs(self, sequential) -> MainlinePlanningInputs:
        """把 authoritative planning DTO 与 Workflow 身份组合为 service 输入。"""

        return MainlinePlanningInputs.model_validate(
            {
                **sequential.model_dump(mode="python"),
                "workflow_run_id": "workflow-mainline",
                "thread_id": "thread-mainline",
            }
        )

    async def _generate(
        self,
        job,
        **_: object,
    ) -> UnitGenerationAttemptResult:
        """为每个 Unit 返回合法独立 Candidate，保留真实校验与 Assembly。"""

        self.calls.append(job)
        tasks = model_tasks(job)
        return UnitGenerationAttemptResult(
            identity=job.identity,
            input_fingerprint=job.context.input_fingerprint,
            raw_response=json.dumps({"tasks": tasks}),
            tasks=tasks,
        )

    async def test_mainline_planning_persists_one_pending_and_keeps_formal(self) -> None:
        """单次 mainline Run 成功后只写一次 Pending，并保持 Formal 字节不变。"""

        formal = confirmed_baseline(project_plan(), execution_scope())
        formal_path = Path(write_build_task_plan_json(self.state, formal))
        formal_bytes = formal_path.read_bytes()
        inputs = self._mainline_inputs(shared_inputs(formal))
        prepare_build_tasks_with_main_agent = Mock(
            side_effect=AssertionError("legacy planner must not be called")
        )

        with patch(
            "app.services.build_task_planning_service.write_pending_build_task_plan_atomic",
            wraps=write_pending_build_task_plan_atomic,
        ) as pending_writer, patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            new=prepare_build_tasks_with_main_agent,
        ), patch(
            "app.services.build_task_planning_service._new_planning_run_id",
            return_value="planning-mainline-single",
        ):
            result = await run_mainline_planning(
                inputs,
                workspace_state=self.state,
                policy=self.policy,
                generate_once=self._generate,
            )

        pending = load_pending_build_task_plan(self.state)
        persisted_run = load_planning_run(self.state)
        identity = validate_pending_self_digest(pending)
        pending_writer.assert_called_once()
        prepare_build_tasks_with_main_agent.assert_not_called()
        self.assertEqual(result.planning_run_id, "planning-mainline-single")
        self.assertEqual(result.planning_status, "validated")
        self.assertEqual(result.terminal_status, "pending_confirmation")
        self.assertEqual(result.draft_identity, identity)
        self.assertEqual(plain_json(result.pending_plan), pending)
        self.assertEqual(persisted_run["planning_run_id"], result.planning_run_id)
        self.assertEqual(formal_path.read_bytes(), formal_bytes)

    async def test_multi_unit_planning_keeps_attempts_and_candidates_independent(self) -> None:
        """两个 Unit 必须分别生成、分别记录 Attempt/Candidate，再共同进入 Assembly。"""

        inputs = self._mainline_inputs(
            planning_inputs(required=["page:a", "page:b"])
        )
        with patch(
            "app.services.build_task_planning_service._new_planning_run_id",
            return_value="planning-mainline-multi",
        ):
            result = await run_mainline_planning(
                inputs,
                workspace_state=self.state,
                policy=self.policy,
                generate_once=self._generate,
            )

        run = result.validated_assembled_plan.planning_run
        self.assertEqual(
            Counter(job.identity.unit_id for job in self.calls),
            {"page:a": 1, "page:b": 1},
        )
        self.assertEqual(
            len({job.identity.attempt_id for job in self.calls}),
            2,
        )
        current_candidates = {
            unit_id: run.candidates[run.unit_states[unit_id].latest_candidate_id]
            for unit_id in run.planning_unit_ids
        }
        self.assertEqual(set(current_candidates), {"page:a", "page:b"})
        self.assertEqual(
            {candidate.identity.unit_id for candidate in current_candidates.values()},
            {"page:a", "page:b"},
        )
        assembled = plain_json(
            result.validated_assembled_plan.assembly.assembled_plan
        )
        self.assertEqual(
            {task["unit_id"] for task in assembled["task_registry"].values()},
            {"page:a", "page:b"},
        )
        self.assertEqual(
            set(result.pending_plan["task_registry"]),
            set(assembled["task_registry"]),
        )

    async def test_planning_failure_does_not_write_pending(self) -> None:
        """Scheduler 基础设施失败必须终止 Run，且绝不调用 Pending writer。"""

        inputs = self._mainline_inputs(planning_inputs(required=["page:a"]))

        async def fail_generation(job, **_: object) -> UnitGenerationAttemptResult:
            """在首个 Unit worker 制造不可重试基础设施失败。"""

            raise UnitGenerationInfrastructureError(
                identity=job.identity,
                stage="model_invoke",
                cause=RuntimeError("mainline planning failed"),
            )

        with patch(
            "app.services.build_task_planning_service.write_pending_build_task_plan_atomic",
            wraps=write_pending_build_task_plan_atomic,
        ) as pending_writer, patch(
            "app.services.build_task_planning_service._new_planning_run_id",
            return_value="planning-mainline-failed",
        ):
            with self.assertRaises(DagPlanningError):
                await run_mainline_planning(
                    inputs,
                    workspace_state=self.state,
                    policy=self.policy,
                    generate_once=fail_generation,
                )

        pending_writer.assert_not_called()
        self.assertIsNone(load_pending_build_task_plan(self.state))
        self.assertFalse(build_task_plan_json_path(self.state).exists())
        self.assertEqual(load_planning_run(self.state)["status"], "failed")

    async def test_cancellation_propagates_and_does_not_write_pending(self) -> None:
        """取消父协程必须持久化 cancelled Run、继续抛取消且不产生 Pending。"""

        inputs = self._mainline_inputs(
            planning_inputs(required=["page:a", "page:b"])
        )
        worker_started = asyncio.Event()
        release_worker = asyncio.Event()

        async def wait_in_worker(job, **_: object) -> UnitGenerationAttemptResult:
            """在 Unit worker 的真实 await 边界等待父协程取消。"""

            worker_started.set()
            await release_worker.wait()
            tasks = model_tasks(job)
            return UnitGenerationAttemptResult(
                identity=job.identity,
                input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}),
                tasks=tasks,
            )

        with patch(
            "app.services.build_task_planning_service.write_pending_build_task_plan_atomic",
            wraps=write_pending_build_task_plan_atomic,
        ) as pending_writer, patch(
            "app.services.build_task_planning_service._new_planning_run_id",
            return_value="planning-mainline-cancelled",
        ):
            planning_task = asyncio.create_task(
                run_mainline_planning(
                    inputs,
                    workspace_state=self.state,
                    policy=self.policy,
                    generate_once=wait_in_worker,
                )
            )
            await asyncio.wait_for(worker_started.wait(), timeout=1)
            planning_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await planning_task

        pending_writer.assert_not_called()
        self.assertIsNone(load_pending_build_task_plan(self.state))
        self.assertEqual(load_planning_run(self.state)["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
