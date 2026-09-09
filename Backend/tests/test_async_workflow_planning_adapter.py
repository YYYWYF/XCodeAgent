"""T11.6.3 async Workflow Planning adapter 的 Graph 级集成测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from langgraph.checkpoint.memory import InMemorySaver

from app.graph.nodes.task_planning_adapter import (
    create_async_workflow_planning_adapter,
)
from app.graph.workflow import build_graph
from app.services.planning_frozen import plain_json
from app.services.unit_generation_contracts import (
    UnitGenerationAttemptResult,
    UnitGenerationPolicy,
)
from app.workspace.planning_run_documents import load_planning_run
from app.workspace.task_documents import (
    build_task_plan_pending_json_path,
    load_build_task_plan_json,
    load_pending_build_task_plan,
    validate_pending_self_digest,
    write_build_task_plan_json,
)
from tests.dag_planning_baseline_fixtures import (
    confirmed_baseline,
    execution_scope,
    formal_artifacts,
    project_plan,
    workspace_snapshot,
    write_json,
)
from tests.dag_planning_orchestrator_fixtures import model_tasks
from tests.test_build_task_reuse_workspace import _ready_template
from tests.test_unit_generation_contracts import _policy_payload


ARTIFACT_PATHS = {
    "requirement_spec": ".xcodeagent/specs/requirement-spec.json",
    "product_plan": ".xcodeagent/plans/product-plan.json",
    "ui_designs": ".xcodeagent/specs/ui-designs.json",
    "technical_plan": ".xcodeagent/plans/technical-plan.json",
}


class AsyncWorkflowPlanningAdapterTests(unittest.IsolatedAsyncioTestCase):
    """验证真实 Graph await 链、Pending 投影和取消传播。"""

    def setUp(self) -> None:
        """创建固定 Unit policy，并为每个测试隔离临时工作区。"""

        self.policy = UnitGenerationPolicy(**_policy_payload())
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        self.plan = project_plan()
        for key, payload in formal_artifacts(self.plan).items():
            write_json(self.workspace, ARTIFACT_PATHS[key], payload)
        self.snapshot_path = write_json(
            self.workspace,
            ".xcodeagent/cache/workspace-snapshot.json",
            workspace_snapshot(),
        )

    def _state(self, scope: dict[str, str]) -> dict:
        """构造与 Workflow runtime 一致的 generation 分支输入。"""

        return {
            "resume_from": "prepare_build_tasks",
            "workspace": str(self.workspace),
            "project_plan": self.plan,
            "workspace_snapshot_path": str(self.snapshot_path),
            "build_execution_scope": scope,
            "owner_session_id": "session-async-adapter",
            "active_run_id": "workflow-async-adapter",
            "active_thread_id": "thread-async-adapter",
        }

    async def _generate(self, job, **_: object) -> UnitGenerationAttemptResult:
        """让真实 Scheduler/Assembly/Global 链消费合法的逐 Unit Candidate。"""

        tasks = model_tasks(job)
        return UnitGenerationAttemptResult(
            identity=job.identity,
            input_fingerprint=job.context.input_fingerprint,
            raw_response=json.dumps({"tasks": tasks}),
            tasks=tasks,
        )

    async def test_default_graph_binds_async_adapter_after_cutover(self) -> None:
        """未显式注入节点时，默认 Graph 必须绑定 async Planning/Confirm adapter。"""

        sentinel_calls: list[dict] = []

        def adapter_factory(**_: object):
            """返回 authority 探针节点，证明默认 Graph 使用 adapter 工厂。"""

            async def sentinel(state: dict) -> dict:
                sentinel_calls.append(dict(state))
                return {
                    "phase": "prepare_build_tasks",
                    "status": "requires_user_input",
                    "clarification": {"mode": "async-adapter-authority-probe"},
                    "timeline": ["prepare_build_tasks"],
                }

            return sentinel

        with patch(
            "app.graph.workflow.create_async_workflow_planning_adapter",
            new=adapter_factory,
        ):
            graph = build_graph(checkpointer=InMemorySaver())
            result = await graph.ainvoke(
                self._state(execution_scope()),
                config={
                    "configurable": {"thread_id": "thread-async-authority-probe"}
                },
            )

        self.assertEqual(len(sentinel_calls), 1)
        self.assertEqual(
            result["clarification"]["mode"],
            "async-adapter-authority-probe",
        )

    async def test_graph_adapter_creates_pending_and_projects_confirmation_state(self) -> None:
        """Graph 必须 await mainline service，并保持 DraftIdentity 与 Formal 分离。"""

        previous_scope = execution_scope(name="orders")
        current_scope = execution_scope(name="customers")
        formal = confirmed_baseline(self.plan, previous_scope)
        formal_path = Path(write_build_task_plan_json(self._state(current_scope), formal))
        formal_bytes = formal_path.read_bytes()
        legacy_planner = Mock(
            side_effect=AssertionError("async adapter must not call legacy planner")
        )
        adapter = create_async_workflow_planning_adapter(
            policy=self.policy,
            generate_once=self._generate,
        )
        graph = build_graph(
            checkpointer=InMemorySaver(),
            prepare_build_tasks_node=adapter,
        )

        with patch(
            "app.graph.nodes.task_planning_adapter.inspect_template_generation_readiness",
            return_value=_ready_template(self.workspace),
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            new=legacy_planner,
        ), patch(
            "app.services.build_task_planning_service._new_planning_run_id",
            return_value="planning-async-adapter",
        ):
            result = await graph.ainvoke(
                self._state(current_scope),
                config={
                    "configurable": {"thread_id": "thread-async-adapter"}
                },
            )

        pending = load_pending_build_task_plan(self._state(current_scope))
        identity = validate_pending_self_digest(pending)
        planning_run = load_planning_run(self._state(current_scope))
        legacy_planner.assert_not_called()
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["planning_run_id"], "planning-async-adapter")
        self.assertEqual(result["draft_digest"], identity.draft_digest)
        self.assertEqual(result["build_task_plan"], pending)
        self.assertEqual(result["build_task_plan"]["confirmation_status"], "pending")
        self.assertEqual(
            result["build_task_plan_confirmation"]["draftIdentity"],
            {
                "ownerSessionId": "session-async-adapter",
                "planningRunId": identity.planning_run_id,
                "draftDigest": identity.draft_digest,
            },
        )
        self.assertEqual(
            result["dag_generation_progress"]["planningRunId"],
            identity.planning_run_id,
        )
        self.assertEqual(result["build_execution_scope"], current_scope)
        self.assertEqual(
            result["last_persisted_build_execution_scope"],
            previous_scope,
        )
        # Formal authority 与 Pending authority 必须是两个独立字段。
        self.assertEqual(result["build_task_plan_path"], str(formal_path))
        self.assertEqual(
            result["pending_build_task_plan_path"],
            str(build_task_plan_pending_json_path(self._state(current_scope))),
        )
        # 正式基线属于上一 scope，本轮只落盘 Pending；两个 persisted 语义不能混用。
        self.assertFalse(result["build_task_plan_persisted"])
        self.assertTrue(result["pending_build_task_plan_persisted"])
        for key in (
            "build_context",
            "build_units",
            "unit_graph",
            "task_registry",
            "task_graph",
            "tasks",
        ):
            self.assertIn(key, result)
        self.assertEqual(planning_run["planning_run_id"], identity.planning_run_id)
        self.assertEqual(formal_path.read_bytes(), formal_bytes)
        self.assertEqual(
            load_build_task_plan_json(formal_path)["confirmation_status"],
            "confirmed",
        )
        self.assertNotEqual(plain_json(pending), load_build_task_plan_json(formal_path))

    async def test_blocked_round_clears_previous_planning_projection(self) -> None:
        """同一 checkpoint 上一轮成功后进入 blocked，必须清空上一轮 PlanningRun 身份。"""

        scope = execution_scope(name="customers")
        formal = confirmed_baseline(self.plan, execution_scope(name="orders"))
        write_build_task_plan_json(self._state(scope), formal)
        adapter = create_async_workflow_planning_adapter(
            policy=self.policy,
            generate_once=self._generate,
        )
        graph = build_graph(
            checkpointer=InMemorySaver(),
            prepare_build_tasks_node=adapter,
        )
        thread = {"configurable": {"thread_id": "thread-blocked-projection"}}

        with patch(
            "app.graph.nodes.task_planning_adapter.inspect_template_generation_readiness",
            return_value=_ready_template(self.workspace),
        ), patch(
            "app.services.build_task_planning_service._new_planning_run_id",
            return_value="planning-blocked-first",
        ):
            first = await graph.ainvoke(self._state(scope), config=thread)
            # ProductPlan 退回未确认，让下一轮在同一 checkpoint 上被前置门禁阻断。
            write_json(
                self.workspace,
                ARTIFACT_PATHS["product_plan"],
                {
                    **formal_artifacts(self.plan)["product_plan"],
                    "confirmation_status": "draft",
                },
            )
            second = await graph.ainvoke(self._state(scope), config=thread)

        self.assertEqual(first["planning_run_id"], "planning-blocked-first")
        self.assertTrue(first["dag_generation_progress"])
        self.assertEqual(second["status"], "requires_user_input")
        self.assertEqual(second["clarification"]["mode"], "build_prerequisite_error")
        self.assertEqual(second["planning_run_id"], "")
        self.assertEqual(second["draft_digest"], "")
        self.assertEqual(second["dag_generation_progress"], {})
        self.assertEqual(second["build_task_plan_confirmation"], {})
        self.assertEqual(second["pending_build_task_plan_path"], "")
        self.assertFalse(second["pending_build_task_plan_persisted"])
        self.assertFalse(second["build_task_plan_persisted"])

    async def test_graph_cancellation_reaches_planning_run_without_detached_work(self) -> None:
        """取消 Graph coroutine 必须沿 await stack 标记 Run cancelled 且不写 Pending。"""

        worker_started = asyncio.Event()
        release_worker = asyncio.Event()

        async def wait_in_worker(job, **_: object) -> UnitGenerationAttemptResult:
            """在真实 Unit worker await 点保持运行，直到父 Graph coroutine 被取消。"""

            worker_started.set()
            await release_worker.wait()
            tasks = model_tasks(job)
            return UnitGenerationAttemptResult(
                identity=job.identity,
                input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}),
                tasks=tasks,
            )

        legacy_planner = Mock(
            side_effect=AssertionError("cancel path must not call legacy planner")
        )
        adapter = create_async_workflow_planning_adapter(
            policy=self.policy,
            generate_once=wait_in_worker,
        )
        graph = build_graph(
            checkpointer=InMemorySaver(),
            prepare_build_tasks_node=adapter,
        )
        scope = execution_scope(name="orders")

        with patch(
            "app.graph.nodes.task_planning_adapter.inspect_template_generation_readiness",
            return_value=_ready_template(self.workspace),
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            new=legacy_planner,
        ), patch(
            "app.services.build_task_planning_service._new_planning_run_id",
            return_value="planning-async-cancelled",
        ):
            graph_task = asyncio.create_task(
                graph.ainvoke(
                    self._state(scope),
                    config={
                        "configurable": {"thread_id": "thread-async-cancelled"}
                    },
                )
            )
            await asyncio.wait_for(worker_started.wait(), timeout=1)
            graph_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await graph_task

        legacy_planner.assert_not_called()
        self.assertIsNone(load_pending_build_task_plan(self._state(scope)))
        self.assertEqual(
            load_planning_run(self._state(scope))["status"],
            "cancelled",
        )


if __name__ == "__main__":
    unittest.main()
