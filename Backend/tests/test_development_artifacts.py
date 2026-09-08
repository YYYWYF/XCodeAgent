"""验证初次开发状态、目录校准与全部产物测试门禁的真实持久化边界。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage, ApplicationLifecycleStatus, PendingInteractionType,
    WorkbenchExecutionStatus,
)
from app.graph.nodes.lifecycle import test_phase_confirmation
from app.graph.subgraphs.testing import integration_test
from app.protocols.workflow.lifecycle import begin_workflow_lifecycle
from app.services.application_lifecycle import (
    application_lifecycle_payload, create_application_lifecycle, end_workbench_execution,
    load_application_lifecycle, persist_workbench_interaction_submission,
    start_workbench_execution, update_workbench_execution, write_application_lifecycle,
)
from app.services.development_artifacts import (
    DevelopmentArtifactsIncompleteError, complete_initial_development,
    refresh_development_artifacts, require_test_entry, test_entry_gate,
)


class DevelopmentArtifactsTests(unittest.TestCase):
    """每个测试使用独立的当前规划与 lifecycle，不推断历史完成状态。"""

    def setUp(self) -> None:
        """准备两个页面、一个接口及一个不计数的实体。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.plans = self.workspace / ".xcodeagent/plans"
        self.plans.mkdir(parents=True)
        self.product = {"confirmation_status": "confirmed", "pages": [{"pageId": "one"}, {"pageId": "two"}]}
        self.technical = {
            "confirmation_status": "confirmed", "entities": [{"id": "entity"}],
            "api_contracts": [{"id": "api", "endpoints": [{"id": "get"}]}],
        }
        self.write_plans()
        state = create_application_lifecycle(application_id="test", application_name="测试")
        state.initialization.stage = ApplicationLifecycleStage.READY_FOR_WORKBENCH
        state.initialization.status = ApplicationLifecycleStatus.COMPLETED
        write_application_lifecycle(self.workspace, state)

    def write_plans(self) -> None:
        """写入已确认的正式目录，用于模拟目录变化。"""

        for name, value in (("product-plan", self.product), ("technical-plan", self.technical)):
            (self.plans / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")

    def start(self, target: str, *, run_id: str | None = None, replaces: str | None = None, initial: bool = True) -> str:
        """通过真实 lifecycle 服务登记单个目标的初次开发或续接。"""

        run_id = run_id or f"run-{target}"
        start_workbench_execution(
            self.workspace, scope="endpoint" if target == "get" else "page",
            target_id=target, page_id=None if target == "get" else target,
            api_contract_id="api" if target == "get" else None,
            thread_id=f"thread-{target}", run_id=run_id,
            phase="development_readiness_gate", initial_development_entry=initial,
            replaces_run_id=replaces,
        )
        return run_id

    def finish(self, target: str) -> None:
        """模拟服务端已经通过 Build 与单测后提交当前目标。"""

        run_id = self.start(target)
        complete_initial_development(self.workspace, run_id=run_id)

    def test_initial_catalog_is_gray_and_entities_do_not_count(self) -> None:
        """目录初始化不因实体数量或规划文件存在而标记开发完成。"""

        state = refresh_development_artifacts(self.workspace)
        gate = test_entry_gate(state)
        self.assertEqual((gate.total, gate.completed, gate.pending), (3, 0, 3))
        self.assertFalse(gate.allowed)
        self.assertNotIn("testEntryGate", json.loads((self.workspace / ".xcodeagent/application-lifecycle.json").read_text()))
        self.assertEqual(application_lifecycle_payload(state)["testEntryGate"]["total"], 3)

    def test_start_wait_fail_retry_and_complete(self) -> None:
        """状态跟随初次执行，等待确认保持紫色，失败灰色且重试可完成。"""

        run_id = self.start("one")
        state = update_workbench_execution(self.workspace, run_id=run_id, phase="unit_test",
            status=WorkbenchExecutionStatus.AWAITING_USER, pending_type=PendingInteractionType.UNIT_TEST_CONFIRMATION)
        self.assertEqual(test_entry_gate(state).in_progress, 1)
        state = update_workbench_execution(self.workspace, run_id=run_id, phase="build", status=WorkbenchExecutionStatus.FAILED)
        self.assertEqual(test_entry_gate(state).pending, 3)
        retry = self.start("one", run_id="retry", replaces=run_id, initial=False)
        state = complete_initial_development(self.workspace, run_id=retry)
        self.assertEqual(state.development_artifacts.pages["one"].initial_development_status, "completed")
        self.assertEqual(state.active_executions[retry].development_purpose, "initial")

    def test_page_does_not_complete_endpoint_and_last_target_unlocks(self) -> None:
        """每个目标独立计数，最后完成项先写盘再解锁。"""

        self.finish("one")
        self.finish("two")
        state = refresh_development_artifacts(self.workspace)
        self.assertEqual(state.development_artifacts.endpoints["api"]["get"].initial_development_status, "pending")
        with self.assertRaises(DevelopmentArtifactsIncompleteError):
            require_test_entry(self.workspace)
        self.finish("get")
        self.assertTrue(test_entry_gate(require_test_entry(self.workspace)).allowed)

    def test_build_and_unit_test_evidence_are_required(self) -> None:
        """真实节点仅在 Build 与单测门均通过后标绿，其他产物未完成时保留等待状态。"""

        run_id = self.start("one")
        state = {"workspace": str(self.workspace), "active_run_id": run_id,
                 "build_execution_scope": {"type": "page", "targetId": "one"}}
        self.assertEqual(test_phase_confirmation(state)["status"], "failed")
        state["build_summary"] = {"status": "completed"}
        self.assertEqual(test_phase_confirmation(state)["status"], "failed")
        self.assertEqual(test_entry_gate(refresh_development_artifacts(self.workspace)).completed, 0)
        state["unit_test_gate_passed"] = True
        state["test_phase_confirmation"] = {"action": "confirm"}
        result = test_phase_confirmation(state)
        self.assertEqual(result["status"], "requires_user_input")
        self.assertFalse(result["clarification"]["testEntryGate"]["allowed"])
        self.assertEqual(test_entry_gate(refresh_development_artifacts(self.workspace)).completed, 1)

    def test_revision_and_late_failure_cannot_reset_completion(self) -> None:
        """二次修改与旧运行失败不能覆盖首次完成证据。"""

        self.finish("one")
        before = refresh_development_artifacts(self.workspace).development_artifacts.pages["one"]
        revision = self.start("one", run_id="revision")
        state = update_workbench_execution(self.workspace, run_id=revision, phase="build", status=WorkbenchExecutionStatus.FAILED)
        self.assertEqual(state.active_executions[revision].development_purpose, "revision")
        state = update_workbench_execution(self.workspace, run_id="run-one", phase="build", status=WorkbenchExecutionStatus.FAILED)
        self.assertEqual(state.development_artifacts.pages["one"], before)
        complete_initial_development(self.workspace, run_id=revision)
        self.assertEqual(refresh_development_artifacts(self.workspace).development_artifacts.pages["one"], before)

    def test_noninitial_execution_cannot_mark_pending_target_complete(self) -> None:
        """普通执行及修订入口不会替未开发目标补记完成。"""

        run_id = self.start("one", initial=False)
        state = complete_initial_development(self.workspace, run_id=run_id)
        self.assertEqual(test_entry_gate(state).completed, 0)

    def test_parallel_writers_and_duplicate_completion(self) -> None:
        """并行完成不同目标不丢记录，重复完成不重复递增版本。"""

        runs = [self.start(target) for target in ("one", "two", "get")]
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda run: complete_initial_development(self.workspace, run_id=run), runs))
        before = require_test_entry(self.workspace)
        after = complete_initial_development(self.workspace, run_id=runs[0])
        self.assertEqual(after.revision, before.revision)
        self.assertEqual(test_entry_gate(after).completed, 3)

    def test_one_failed_parallel_execution_does_not_clear_other_progress(self) -> None:
        """同目标仍有初次执行时，另一个执行停止不把圆点改成灰色。"""

        first = self.start("one")
        second = self.start("one", run_id="second")
        state = end_workbench_execution(self.workspace, run_id=first)
        self.assertEqual(test_entry_gate(state).in_progress, 1)
        state = end_workbench_execution(self.workspace, run_id=second)
        self.assertEqual(test_entry_gate(state).in_progress, 0)

    def test_catalog_add_remove_rename_and_late_completion(self) -> None:
        """新 ID 待开发、同 ID 改名保留完成、删除目标不被迟到事件复活。"""

        self.finish("one")
        old_run = self.start("two")
        self.product["pages"] = [{"pageId": "one", "name": "新名称"}, {"pageId": "three"}]
        self.write_plans()
        state = complete_initial_development(self.workspace, run_id=old_run)
        self.assertEqual(set(state.development_artifacts.pages), {"one", "three"})
        self.assertEqual(test_entry_gate(state).completed, 1)

    def test_draft_corrupt_and_empty_catalog_fail_closed(self) -> None:
        """未确认草稿保留已完成事实，损坏目录和空目录不放行。"""

        self.finish("one")
        self.product["confirmation_status"] = "pending"
        self.product["pages"] = [{"pageId": "new"}]
        self.write_plans()
        state = refresh_development_artifacts(self.workspace)
        self.assertIn("one", state.development_artifacts.pages)
        self.assertNotIn("new", state.development_artifacts.pages)
        self.assertFalse(test_entry_gate(state).allowed)
        (self.plans / "product-plan.json").write_text("broken", encoding="utf-8")
        with self.assertRaises(DevelopmentArtifactsIncompleteError):
            require_test_entry(self.workspace)
        self.product = {"confirmation_status": "confirmed", "pages": []}
        self.technical["api_contracts"] = []
        self.write_plans()
        gate = test_entry_gate(refresh_development_artifacts(self.workspace))
        self.assertEqual(gate.total, 0)
        self.assertFalse(gate.allowed)

    def test_blocked_confirmation_does_not_consume_interaction(self) -> None:
        """全量门禁拒绝后保留原执行及其待确认令牌。"""

        self.finish("one")
        state = update_workbench_execution(self.workspace, run_id="run-one", phase="test_phase_confirmation",
            status=WorkbenchExecutionStatus.AWAITING_USER, pending_type=PendingInteractionType.TEST_PHASE_CONFIRMATION)
        pending = state.active_executions["run-one"].pending_interaction
        with self.assertRaises(DevelopmentArtifactsIncompleteError):
            persist_workbench_interaction_submission(self.workspace, run_id="run-one",
                interaction_id=pending.id, based_on_revision=pending.based_on_revision)
        after = load_application_lifecycle(self.workspace)
        self.assertIsNone(after.active_executions["run-one"].pending_interaction.submitted_at)

    def test_direct_test_start_and_graph_entry_are_guarded(self) -> None:
        """直接指定集成测试节点不能登记执行或启动子图。"""

        with self.assertRaises(DevelopmentArtifactsIncompleteError):
            begin_workflow_lifecycle({"workspace": str(self.workspace), "resume_values": {}},
                thread_id="test-thread", run_id="test-run", phase="integration_test")
        self.assertNotIn("test-run", load_application_lifecycle(self.workspace).active_executions)
        with patch("app.graph.subgraphs.testing._testing_subgraph.invoke") as invoke:
            with self.assertRaises(DevelopmentArtifactsIncompleteError):
                integration_test({"workspace": str(self.workspace)})
            invoke.assert_not_called()

    def test_write_failure_never_publishes_completion(self) -> None:
        """完成落盘失败时，重读仍未完成且不会返回允许测试的快照。"""

        run_id = self.start("one")
        with patch("app.services.application_lifecycle.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                complete_initial_development(self.workspace, run_id=run_id)
        self.assertEqual(test_entry_gate(load_application_lifecycle(self.workspace)).completed, 0)

    def test_test_handoff_write_failure_preserves_source_confirmation(self) -> None:
        """测试 execution 原子接替失败时，原确认令牌与原运行仍可重试。"""

        for target in ("one", "two", "get"):
            self.finish(target)
        state = update_workbench_execution(self.workspace, run_id="run-one", phase="test_phase_confirmation",
            status=WorkbenchExecutionStatus.AWAITING_USER, pending_type=PendingInteractionType.TEST_PHASE_CONFIRMATION)
        pending = state.active_executions["run-one"].pending_interaction
        inputs = {"workspace": str(self.workspace), "resume_values": {
            "selectedPageId": "one", "build_execution_scope": {"type": "page", "targetId": "one"},
            "test_phase_confirmation": {"action": "confirm"},
            "resume_execution_run_id": "run-one",
            "lifecycle_interaction_submission": {
                "runId": "run-one", "id": pending.id, "basedOnRevision": pending.based_on_revision,
            },
        }}
        with patch("app.services.application_lifecycle.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                begin_workflow_lifecycle(inputs, thread_id="test-thread", run_id="test-run", phase="test_phase_confirmation")
        after = load_application_lifecycle(self.workspace)
        self.assertNotIn("test-run", after.active_executions)
        self.assertEqual(after.active_executions["run-one"].pending_interaction, pending)
        result = begin_workflow_lifecycle(inputs, thread_id="test-thread", run_id="test-run", phase="test_phase_confirmation")
        self.assertTrue(result["testEntryGate"]["allowed"])
        self.assertNotIn("run-one", result["activeExecutions"])

    def test_gate_rejection_emits_complete_ag_ui_lifecycle(self) -> None:
        """真实 AG-UI 运行被门禁拦截时，输出结构化错误、快照与正常终帧。"""

        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
        from app.graph.state import ProjectState
        from app.protocols.workflow import build_workflow_ag_ui_stream

        def guarded_node(state: ProjectState) -> dict:
            """在模型或测试命令执行前触发真实门禁。"""

            require_test_entry(state["workspace"])
            return {"status": "completed"}

        builder = StateGraph(ProjectState)
        builder.add_node("development_readiness_gate", guarded_node)
        builder.add_edge(START, "development_readiness_gate")
        builder.add_edge("development_readiness_gate", END)
        graph = builder.compile(checkpointer=InMemorySaver())

        async def collect() -> list[dict]:
            """消费全部 AG-UI 帧，验证异常也能收口完整生命周期。"""

            frames = [frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload={
                "threadId": "stream-thread", "runId": "stream-run",
                "messages": [{"role": "user", "content": "开发页面"}],
                "forwardedProps": {"workspaceRoot": str(self.workspace), "selectedPageId": "one"},
            })]
            return [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]

        with patch("app.protocols.workflow.request._project_plan_start_values", return_value={}):
            events = asyncio.run(collect())
        types = [event["type"] for event in events]
        self.assertEqual(types[0], "RUN_STARTED")
        self.assertEqual(types[-1], "RUN_FINISHED")
        self.assertNotIn("RUN_ERROR", types)
        self.assertIn("STATE_SNAPSHOT", types)
        self.assertIn("TEXT_MESSAGE_CONTENT", types)
        final = events[-1]["result"]["workflow"]
        self.assertEqual(final["summary"]["errorCode"], "development_artifacts_incomplete")
        self.assertFalse(final["summary"]["lifecycle"]["testEntryGate"]["allowed"])
        self.assertEqual(load_application_lifecycle(self.workspace).active_executions["stream-run"].status, "awaiting_user")

    def test_last_completion_reaches_ag_ui_before_user_test_confirmation(self) -> None:
        """真实节点与协议联动：最后一个目标标绿后等待确认，不自动开始测试。"""

        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
        from app.graph.state import ProjectState
        from app.protocols.workflow import build_workflow_ag_ui_stream

        self.product["pages"] = [{"pageId": "one"}]
        self.technical["api_contracts"] = []
        self.write_plans()

        def successful_development(state: ProjectState) -> dict:
            """以确定性测试节点提供服务端 Build 和单测完成证据。"""

            return {"phase": "build", "status": "completed", "build_summary": {"status": "completed"},
                    "unit_test_gate_passed": True}

        builder = StateGraph(ProjectState)
        builder.add_node("development_readiness_gate", successful_development)
        builder.add_node("test_phase_confirmation", test_phase_confirmation)
        builder.add_edge(START, "development_readiness_gate")
        builder.add_edge("development_readiness_gate", "test_phase_confirmation")
        builder.add_edge("test_phase_confirmation", END)
        graph = builder.compile(checkpointer=InMemorySaver())

        async def collect() -> list[dict]:
            """消费完成帧并检查当前 lifecycle，而不是客户端推断的状态。"""

            frames = [frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload={
                "threadId": "complete-thread", "runId": "complete-run",
                "messages": [{"role": "user", "content": "开发页面"}],
                "forwardedProps": {"workspaceRoot": str(self.workspace), "selectedPageId": "one"},
            })]
            return [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]

        with patch("app.protocols.workflow.request._project_plan_start_values", return_value={}):
            events = asyncio.run(collect())
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        lifecycle_events = [event["value"] for event in events if event.get("name") == "application-lifecycle"]
        self.assertEqual(lifecycle_events[0]["developmentArtifacts"]["pages"]["one"]["initialDevelopmentStatus"], "in_progress")
        self.assertTrue(lifecycle_events[-1]["testEntryGate"]["allowed"])
        self.assertEqual(lifecycle_events[-1]["activeExecutions"]["complete-run"]["status"], "awaiting_user")
        self.assertEqual(events[-1]["result"]["workflow"]["summary"]["lifecycle"]["testEntryGate"]["completed"], 1)
