"""T11.6.4 cutover acceptance：production Workflow 真正走 async Planning authority。

T11.6.1 起草时把「production 不再调用 legacy planner」放在 T11.6.1；但 T11.6.3 明确
要求默认 production graph 在 T11.6.4 之前继续绑定 legacy 节点，两条要求在 cutover 前
不可能同时成立。cutover 后本文件不再跳过，并从真实编译的 production Graph 入口验证：
PlanningRun 由 service 创建、Scheduler 真正调度 Unit、Scope Assembly 执行、Pending 落盘、
legacy planner/legacy Confirm 均零调用。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from langgraph.checkpoint.memory import InMemorySaver

from app.graph.workflow import build_graph
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

        legacy_planner = Mock(
            side_effect=AssertionError("production Workflow 仍调用 legacy scope-level planner")
        )
        legacy_confirm = Mock(
            side_effect=AssertionError("production Workflow 仍调用 legacy Confirm 直写 Formal")
        )

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
        ), patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            new=legacy_planner,
        ), patch(
            "app.graph.nodes.tasks._handle_build_task_plan_confirmation",
            new=legacy_confirm,
        ):
            result = await graph.ainvoke(
                self._state(scope),
                config={"configurable": {"thread_id": "thread-production-cutover"}},
            )

        legacy_planner.assert_not_called()
        legacy_confirm.assert_not_called()
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
        legacy_planner = Mock(
            side_effect=AssertionError("production Workflow 仍调用 legacy planner")
        )

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
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            new=legacy_planner,
        ):
            result = await graph.ainvoke(
                self._state(scope),
                config={"configurable": {"thread_id": "thread-multi-unit"}},
            )

        legacy_planner.assert_not_called()
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


if __name__ == "__main__":
    unittest.main()
