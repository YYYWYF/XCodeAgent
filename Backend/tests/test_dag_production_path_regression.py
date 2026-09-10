"""T11.6.4 cutover acceptance：production Workflow 真正走 async Planning authority。

本文件从真实编译的 production Graph 入口验证：PlanningRun 由 service 创建、Scheduler
真正调度 Unit、Scope Assembly 执行、Pending 落盘，并确认旧 Scope Planner 源码已删除。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from app.graph.workflow import build_graph
from app.protocols.workflow.run_control import build_workflow_plan_control_ag_ui_stream
from app.services.dag_planning_orchestrator import UnitGenerationScheduler
from app.services.scope_assembly import assemble_scope_build_task_plan
from app.services.unit_generation_contracts import UnitGenerationAttemptResult
from app.workspace.planning_run_documents import load_planning_run
from app.workspace.task_documents import (
    build_task_plan_json_path,
    load_pending_build_task_plan,
    validate_pending_self_digest,
)
from tests.dag_planning_baseline_fixtures import (
    execution_scope,
    formal_artifacts,
    project_plan,
    write_confirmed_endpoint_designs,
    workspace_snapshot,
    write_json,
)
from tests.dag_planning_orchestrator_fixtures import model_tasks
from tests.test_build_task_reuse_workspace import _ready_template


ARTIFACT_PATHS = {
    "requirement_spec": ".xcodeagent/specs/requirement-spec.json",
    "product_plan": ".xcodeagent/plans/product-plan.json",
    "ui_designs": ".xcodeagent/specs/ui-designs.json",
    "technical_plan": ".xcodeagent/plans/technical-plan.json",
}


class DagProductionPathCutoverTests(unittest.IsolatedAsyncioTestCase):
    """验证 production Workflow 的 DAG planning 实际调用边界。"""

    def setUp(self) -> None:
        """为每个测试隔离临时工作区，并写入门禁所需正式产物。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        self.plan = project_plan()
        for key, payload in formal_artifacts(self.plan).items():
            write_json(self.workspace, ARTIFACT_PATHS[key], payload)
        write_confirmed_endpoint_designs(self.workspace, self.plan)
        self.snapshot_path = write_json(
            self.workspace,
            ".xcodeagent/cache/workspace-snapshot.json",
            workspace_snapshot(),
        )

    def _state(self, scope: dict) -> dict:
        """构造与 Workflow runtime 一致的 production 入口输入。"""

        return {
            "resume_from": "prepare_build_tasks",
            "workspace": str(self.workspace),
            "project_plan": self.plan,
            "workspace_snapshot_path": str(self.snapshot_path),
            "build_execution_scope": scope,
            "owner_session_id": "session-production-cutover",
            "active_run_id": "workflow-production-cutover",
            "active_thread_id": "thread-production-cutover",
        }

    def _model_stub(self, seen_units: list[str]):
        """记录被调度的 Unit，并返回该 Unit 的严格候选正文。"""

        async def generate(job, **_: object) -> UnitGenerationAttemptResult:
            seen_units.append(job.identity.unit_id)
            tasks = model_tasks(job)
            return UnitGenerationAttemptResult(
                identity=job.identity,
                input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}),
                tasks=tasks,
            )

        return generate

    def _scheduler_spy(self, rounds: list[str]):
        """包装真实 Scheduler，记录 run_round 被调用后仍执行真实调度。"""

        real_scheduler = UnitGenerationScheduler

        class _SpyScheduler(real_scheduler):
            async def run_round(self, *args, **kwargs):
                rounds.append(args[0].snapshot.planning_run_id)
                return await super().run_round(*args, **kwargs)

        return _SpyScheduler

    def test_removed_scope_planner_sources_are_absent(self) -> None:
        """旧 Scope Planner 源文件必须删除，避免仓库保留第二套规划真相。"""

        repository = Path(__file__).resolve().parents[1]
        for relative_path in (
            "app/agents/main/task_preparer.py",
            "app/agents/main/task_preparer_prompt.py",
            "tests/test_prepare_build_tasks_guard.py",
        ):
            self.assertFalse((repository / relative_path).exists(), relative_path)

    async def test_default_production_graph_runs_async_planning_authority(self) -> None:
        """默认 build_graph 必须创建 Run、调度 Scheduler、执行 Assembly 并落 Pending。"""

        scope = execution_scope(name="customers")
        seen_units: list[str] = []
        scheduler_rounds: list[str] = []
        assembly_calls: list[dict] = []
        real_assemble = assemble_scope_build_task_plan

        def assemble_spy(*args, **kwargs):
            assembly_calls.append((kwargs.get("build_context") or {}).get("scope") or {})
            return real_assemble(*args, **kwargs)

        # 默认 Graph：不注入任何节点，必须由 workflow.py 绑定 async adapter。
        graph = build_graph(checkpointer=InMemorySaver())
        with patch(
            "app.graph.nodes.task_planning_adapter.inspect_template_generation_readiness",
            return_value=_ready_template(self.workspace),
        ), patch(
            "app.services.dag_planning_orchestrator.generate_unit_candidate_once",
            new=self._model_stub(seen_units),
        ), patch(
            "app.services.dag_planning_orchestrator.UnitGenerationScheduler",
            new=self._scheduler_spy(scheduler_rounds),
        ), patch(
            "app.services.dag_planning_orchestrator.assemble_scope_build_task_plan",
            new=assemble_spy,
        ):
            result = await graph.ainvoke(
                self._state(scope),
                config={"configurable": {"thread_id": "thread-production-cutover"}},
            )

        # PlanningRun 由 production service 拥有并落盘。
        run = load_planning_run(self._state(scope))
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "active")
        self.assertEqual(run["phase"], "persisting_pending")
        # Scheduler 与 Scope Assembly 真实执行。
        self.assertEqual(scheduler_rounds, [run["planning_run_id"]])
        self.assertEqual(assembly_calls, [scope])
        # 至少一个 Unit 由模型 worker 生成。
        self.assertTrue(seen_units)
        # Pending 落盘，Formal 未被写入。
        pending = load_pending_build_task_plan(self._state(scope))
        self.assertIsNotNone(pending)
        identity = validate_pending_self_digest(pending)
        self.assertFalse(build_task_plan_json_path(self._state(scope)).exists())
        # Graph 停在等用户确认，并投影精确 DraftIdentity。
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["planning_run_id"], run["planning_run_id"])
        self.assertEqual(result["draft_digest"], identity.draft_digest)
        self.assertEqual(
            result["build_task_plan_confirmation"]["draftIdentity"],
            {
                "ownerSessionId": "session-production-cutover",
                "planningRunId": identity.planning_run_id,
                "draftDigest": identity.draft_digest,
            },
        )
        self.assertTrue(result["dag_generation_progress"])

    async def test_production_scope_generates_multiple_units(self) -> None:
        """page scope 必须调度多个 Unit 独立生成，再统一 Scope Assembly。"""

        scope = execution_scope(name="orders")
        seen_units: list[str] = []
        scheduler_rounds: list[str] = []
        graph = build_graph(checkpointer=InMemorySaver())
        with patch(
            "app.graph.nodes.task_planning_adapter.inspect_template_generation_readiness",
            return_value=_ready_template(self.workspace),
        ), patch(
            "app.services.dag_planning_orchestrator.generate_unit_candidate_once",
            new=self._model_stub(seen_units),
        ), patch(
            "app.services.dag_planning_orchestrator.UnitGenerationScheduler",
            new=self._scheduler_spy(scheduler_rounds),
        ):
            result = await graph.ainvoke(
                self._state(scope),
                config={"configurable": {"thread_id": "thread-multi-unit"}},
            )

        # 至少两个不同 Unit 各自独立产生候选。
        unique_units = set(seen_units)
        self.assertGreaterEqual(len(unique_units), 2)
        # 每个 Unit 只在自己那一轮被调度一次（同一 Run 内身份独立）。
        self.assertEqual(len(seen_units), len(unique_units))
        pending = load_pending_build_task_plan(self._state(scope))
        self.assertIsNotNone(pending)
        # 多个 Unit 的候选经 Scope Assembly 汇总进同一 Pending。
        self.assertGreaterEqual(len(pending["build_units"]), 2)
        self.assertTrue(unique_units <= set(pending["build_units"]))
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["build_execution_scope"], scope)

    async def test_abandon_ends_generation_and_discards_pending(self) -> None:
        """Abandon 必须结束本次 DAG 生成、丢弃 Pending，且绝不写 Formal。"""

        scope = execution_scope(name="orders")
        graph = build_graph(checkpointer=InMemorySaver())
        with patch(
            "app.graph.nodes.task_planning_adapter.inspect_template_generation_readiness",
            return_value=_ready_template(self.workspace),
        ), patch(
            "app.services.dag_planning_orchestrator.generate_unit_candidate_once",
            new=self._model_stub([]),
        ):
            result = await graph.ainvoke(
                self._state(scope),
                config={"configurable": {"thread_id": "thread-production-abandon"}},
            )

        identity = result["build_task_plan_confirmation"]["draftIdentity"]
        # 生成成功后 Run 仍在磁盘上，Abandon 必须把它收口。
        self.assertEqual(load_planning_run(self._state(scope))["status"], "active")
        self.assertIsNotNone(load_pending_build_task_plan(self._state(scope)))

        frames = "".join(
            [
                frame
                async for frame in build_workflow_plan_control_ag_ui_stream(
                    action="abandon",
                    workspace=str(self.workspace),
                    target_run_id="workflow-production-cutover",
                    planning_run_id=identity["planningRunId"],
                    draft_digest=identity["draftDigest"],
                    thread_id="thread-production-cutover",
                    run_id="request-production-abandon",
                )
            ]
        )

        self.assertIn('"status":"abandoned"', frames)
        # 本次生成的 Pending 彻底丢弃，DAG 生成结束（PlanningRun 不再存在）。
        self.assertIsNone(load_pending_build_task_plan(self._state(scope)))
        self.assertIsNone(load_planning_run(self._state(scope)))
        # Formal 从不写入。
        self.assertFalse(build_task_plan_json_path(self._state(scope)).exists())


if __name__ == "__main__":
    unittest.main()
