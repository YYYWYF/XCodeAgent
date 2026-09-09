"""T11.6.4 Confirm authority cutover：Graph 只经 lifecycle 提升 Formal。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from langgraph.checkpoint.memory import InMemorySaver

from app.graph.nodes.task_planning_adapter import (
    create_async_workflow_planning_adapter,
)
from app.graph.workflow import build_graph
from app.protocols.workflow.request import workflow_run_inputs
from app.services.unit_generation_contracts import UnitGenerationAttemptResult
from app.workspace.task_documents import (
    build_task_plan_json_path,
    build_task_plan_pending_json_path,
    load_confirmed_build_task_plan,
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


class DagConfirmAuthorityCutoverTests(unittest.IsolatedAsyncioTestCase):
    """从真实 production Graph 串起生成、确认、Formal 投影与 Build 放行。"""

    def setUp(self) -> None:
        """隔离工作区并写入门禁正式产物。"""

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
        self.readiness = _ready_template(self.workspace)
        self.scope = execution_scope(name="customers")

    def _state(self, **overrides: object) -> dict:
        """构造 production 入口输入，允许覆盖确认动作。"""

        return {
            "resume_from": "prepare_build_tasks",
            "workspace": str(self.workspace),
            "project_plan": self.plan,
            "workspace_snapshot_path": str(self.snapshot_path),
            "build_execution_scope": self.scope,
            "active_run_id": "workflow-confirm-cutover",
            "active_thread_id": "thread-confirm-cutover",
            **overrides,
        }

    def _model_stub(self):
        """返回严格候选正文的模型替身。"""

        async def generate(job, **_: object) -> UnitGenerationAttemptResult:
            tasks = model_tasks(job)
            return UnitGenerationAttemptResult(
                identity=job.identity,
                input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}),
                tasks=tasks,
            )

        return generate

    async def _generate_pending(self, graph, thread_id: str) -> tuple[dict, dict]:
        """执行一次真实 generation，返回 Graph 结果与 Pending DraftIdentity。"""

        with patch(
            "app.graph.nodes.task_planning_adapter.inspect_template_generation_readiness",
            return_value=self.readiness,
        ), patch(
            "app.services.dag_planning_orchestrator.generate_unit_candidate_once",
            new=self._model_stub(),
        ):
            result = await graph.ainvoke(
                self._state(),
                config={"configurable": {"thread_id": thread_id}},
            )
        pending = load_pending_build_task_plan(self._state())
        self.assertIsNotNone(pending)
        return result, validate_pending_self_digest(pending).model_dump(mode="json")

    async def test_confirm_resume_promotes_formal_and_releases_build(self) -> None:
        """generation→Pending→确认→lifecycle Formal→Graph 投影→Build 放行。"""

        build_states: list[dict] = []
        legacy_confirm = Mock(
            side_effect=AssertionError("Confirm 不得走 legacy 直写 Formal")
        )
        legacy_planner = Mock(
            side_effect=AssertionError("Confirm 不得回退 legacy planner")
        )

        def build_spy(state: dict) -> dict:
            build_states.append(dict(state))
            return {"status": "completed", "phase": "build"}

        # build 节点必须在编译前替换，否则 Confirm 放行后真实 Build 会启动。
        with patch("app.graph.nodes.build", new=build_spy):
            graph = build_graph(checkpointer=InMemorySaver())
            thread = "thread-confirm-cutover"
            generated, identity = await self._generate_pending(graph, thread)
            self.assertEqual(generated["status"], "requires_user_input")
            self.assertFalse(build_task_plan_json_path(self._state()).exists())

            confirm_state = self._state(
                build_task_plan_confirmation={
                    "mode": "build_task_plan_confirmation",
                    "action": "confirm",
                    "planning_run_id": identity["planning_run_id"],
                    "draft_digest": identity["draft_digest"],
                }
            )
            with patch(
                "app.graph.nodes.task_planning_adapter.inspect_template_generation_readiness",
                return_value=self.readiness,
            ), patch(
                "app.graph.nodes.tasks._handle_build_task_plan_confirmation",
                new=legacy_confirm,
            ), patch(
                "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
                new=legacy_planner,
            ):
                confirmed = await graph.ainvoke(
                    confirm_state,
                    config={"configurable": {"thread_id": thread}},
                )

        legacy_confirm.assert_not_called()
        legacy_planner.assert_not_called()
        # lifecycle Confirm 是唯一提升 authority：Formal 带精确 confirmed_from。
        formal = load_confirmed_build_task_plan(self.workspace)
        self.assertIsNotNone(formal)
        self.assertEqual(formal["confirmation_status"], "confirmed")
        self.assertEqual(
            formal["confirmed_from"],
            {
                "planning_run_id": identity["planning_run_id"],
                "draft_digest": identity["draft_digest"],
            },
        )
        # Pending 在提交后被清理，Formal 保留。
        self.assertFalse(build_task_plan_pending_json_path(self._state()).exists())
        # Graph 把 Formal 投影为已确认状态并放行到 Build 节点。
        self.assertEqual(confirmed["clarification"], {})
        self.assertEqual(confirmed["build_task_plan"]["confirmation_status"], "confirmed")
        self.assertEqual(
            confirmed["build_task_plan_path"],
            str(build_task_plan_json_path(self._state())),
        )
        self.assertEqual(len(build_states), 1)
        self.assertEqual(build_states[0]["clarification"], {})
        self.assertEqual(
            build_states[0]["build_task_plan"]["confirmation_status"], "confirmed"
        )
        self.assertEqual(
            build_states[0]["build_task_plan_path"],
            str(build_task_plan_json_path(self._state())),
        )

    async def test_stale_draft_confirm_is_rejected_and_keeps_new_pending(self) -> None:
        """Pending A 被 B 替换后，用 A 的 DraftIdentity 确认必须 stale。"""

        graph = build_graph(checkpointer=InMemorySaver())
        _, identity_a = await self._generate_pending(graph, "thread-stale-a")
        _, identity_b = await self._generate_pending(graph, "thread-stale-b")
        self.assertNotEqual(
            identity_a["planning_run_id"], identity_b["planning_run_id"]
        )
        pending_b_path = build_task_plan_pending_json_path(self._state())
        pending_b_bytes = pending_b_path.read_bytes()

        stale_state = self._state(
            build_task_plan_confirmation={
                "mode": "build_task_plan_confirmation",
                "action": "confirm",
                "planning_run_id": identity_a["planning_run_id"],
                "draft_digest": identity_a["draft_digest"],
            }
        )
        with patch(
            "app.graph.nodes.task_planning_adapter.inspect_template_generation_readiness",
            return_value=self.readiness,
        ):
            result = await graph.ainvoke(
                stale_state,
                config={"configurable": {"thread_id": "thread-stale-a"}},
            )

        # 过期确认不写 Formal，也不覆盖较新的 Pending B。
        self.assertFalse(build_task_plan_json_path(self._state()).exists())
        self.assertEqual(pending_b_path.read_bytes(), pending_b_bytes)
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["planning_run_id"], identity_b["planning_run_id"])
        self.assertEqual(result["draft_digest"], identity_b["draft_digest"])
        self.assertTrue(result["build_task_plan_confirmation"]["errors"])

    async def test_regenerate_resume_consumes_old_pending_and_projects_new_identity(self) -> None:
        """结构化 Regenerate 应回到同一节点并生成全新 Pending identity。"""

        graph = build_graph(checkpointer=InMemorySaver())
        thread = "thread-regenerate-cutover"
        _, old_identity = await self._generate_pending(graph, thread)

        with patch(
            "app.graph.nodes.task_planning_adapter.inspect_template_generation_readiness",
            return_value=self.readiness,
        ), patch(
            "app.services.dag_planning_orchestrator.generate_unit_candidate_once",
            new=self._model_stub(),
        ), patch(
            "app.services.dag_planning_regeneration._new_planning_run_id",
            return_value="planning-regenerated-cutover",
        ):
            regenerated = await graph.ainvoke(
                self._state(
                    build_task_plan_confirmation={
                        "mode": "build_task_plan_confirmation",
                        "action": "regenerate",
                        "planning_run_id": old_identity["planning_run_id"],
                        "draft_digest": old_identity["draft_digest"],
                    }
                ),
                config={"configurable": {"thread_id": thread}},
            )

        pending = load_pending_build_task_plan(self._state())
        new_identity = validate_pending_self_digest(pending)
        self.assertEqual(regenerated["status"], "requires_user_input")
        self.assertEqual(new_identity.planning_run_id, "planning-regenerated-cutover")
        self.assertNotEqual(new_identity.planning_run_id, old_identity["planning_run_id"])
        self.assertNotEqual(new_identity.draft_digest, old_identity["draft_digest"])
        self.assertEqual(regenerated["planning_run_id"], new_identity.planning_run_id)
        self.assertEqual(regenerated["draft_digest"], new_identity.draft_digest)
        self.assertEqual(
            regenerated["build_task_plan_confirmation"]["actionValues"],
            ["confirm", "abandon", "regenerate"],
        )
        self.assertFalse(build_task_plan_json_path(self._state()).exists())

    async def test_non_confirm_action_never_reaches_confirm_service(self) -> None:
        """非 confirm 的 build_task_plan_confirmation 必须 fail closed，不提升 Formal。"""

        graph = build_graph(checkpointer=InMemorySaver())
        _, identity = await self._generate_pending(graph, "thread-non-confirm")
        pending_path = build_task_plan_pending_json_path(self._state())
        pending_bytes = pending_path.read_bytes()
        confirm_spy = Mock(
            side_effect=AssertionError("非 confirm action 不得调用 confirm_service")
        )
        adapter = create_async_workflow_planning_adapter(confirm_service=confirm_spy)
        guarded_graph = build_graph(
            checkpointer=InMemorySaver(),
            prepare_build_tasks_node=adapter,
        )

        with patch(
            "app.graph.nodes.task_planning_adapter.inspect_template_generation_readiness",
            return_value=self.readiness,
        ):
            result = await guarded_graph.ainvoke(
                self._state(
                    build_task_plan_confirmation={
                        "mode": "build_task_plan_confirmation",
                        "action": "abandon",
                        "planning_run_id": identity["planning_run_id"],
                        "draft_digest": identity["draft_digest"],
                    }
                ),
                config={"configurable": {"thread_id": "thread-non-confirm"}},
            )

        # 非法动作 fail closed：Confirm authority 零调用、Formal 未写入、Pending 原样保留。
        confirm_spy.assert_not_called()
        self.assertFalse(build_task_plan_json_path(self._state()).exists())
        self.assertEqual(pending_path.read_bytes(), pending_bytes)
        self.assertEqual(result["status"], "requires_user_input")
        self.assertTrue(result["build_task_plan_confirmation"]["errors"])

    def test_request_parser_forwards_exact_draft_identity(self) -> None:
        """协议层把前端 camelCase 身份转发为 adapter 读取的 snake_case 字段。"""

        digest = "b" * 64
        inputs = workflow_run_inputs(
            {
                "request": "确认任务规划",
                "clarificationAnswers": {
                    "build_task_plan_confirmation": {
                        "action": "confirm",
                        "planningRunId": "planning-forward-1",
                        "draftDigest": digest,
                    }
                },
            }
        )
        self.assertEqual(inputs["resume_from"], "prepare_build_tasks")
        self.assertEqual(
            inputs["resume_values"]["build_task_plan_confirmation"],
            {
                "mode": "build_task_plan_confirmation",
                "action": "confirm",
                "planning_run_id": "planning-forward-1",
                "draft_digest": digest,
            },
        )

    def test_request_parser_accepts_snake_case_and_rejects_abandon(self) -> None:
        """兼容 snake_case 身份和 Regenerate；abandon 仍由计划控制流处理。"""

        digest = "c" * 64
        forwarded = workflow_run_inputs(
            {
                "request": "确认任务规划",
                "clarificationAnswers": {
                    "build_task_plan_confirmation": {
                        "action": "confirm",
                        "planning_run_id": "planning-forward-2",
                        "draft_digest": digest,
                    }
                },
            }
        )
        forwarded_confirmation = forwarded["resume_values"][
            "build_task_plan_confirmation"
        ]
        self.assertEqual(
            forwarded_confirmation["planning_run_id"], "planning-forward-2"
        )
        self.assertEqual(forwarded_confirmation["draft_digest"], digest)
        regenerated = workflow_run_inputs(
            {
                "request": "重新生成任务规划",
                "clarificationAnswers": {
                    "build_task_plan_confirmation": {
                        "action": "regenerate",
                        "planningRunId": "planning-forward-2",
                        "draftDigest": digest,
                    }
                },
            }
        )
        self.assertEqual(regenerated["resume_from"], "prepare_build_tasks")
        self.assertEqual(
            regenerated["resume_values"]["build_task_plan_confirmation"],
            {
                "mode": "build_task_plan_confirmation",
                "action": "regenerate",
                "planning_run_id": "planning-forward-2",
                "draft_digest": digest,
            },
        )
        abandoned = workflow_run_inputs(
            {
                "request": "放弃任务规划",
                "clarificationAnswers": {
                    "build_task_plan_confirmation": {
                        "action": "abandon",
                        "planningRunId": "planning-forward-2",
                        "draftDigest": digest,
                    }
                },
            }
        )
        self.assertEqual(
            abandoned["resume_values"].get("build_task_plan_confirmation", {}), {}
        )


if __name__ == "__main__":
    unittest.main()
