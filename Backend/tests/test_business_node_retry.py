"""验证按节点重试不复制旧 State，磁盘资产优先且无入口不占用新会话。"""

import json
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from langgraph.graph import START, END
from langgraph.types import Command
from langgraph.checkpoint.memory import InMemorySaver

from app.domain.execution_recovery import DurableExecutionRecord
from app.services.business_node_retry import read_retry_hint, retry_action_plan, release_unretryable_execution, prepare_business_node_retry
from app.services.stage_artifacts import ArtifactStateGraph, load_stage_artifacts
from app.services.application_lifecycle import start_workbench_execution, load_application_lifecycle
from app.protocols.workflow.lifecycle import fail_workflow_lifecycle
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class BusinessNodeRetryTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 Graph 和工作区文件验证节点重执行合同。"""

    def test_planning_reentry_does_not_read_or_validate_lifecycle_stage(self):
        """业务重试可直接进入任一规划节点，不再因落盘后的阶段推进被入口拒绝。"""

        from app.graph.application_planning_workflow import _route_start
        nodes = (
            "requirements", "product_planning", "ui_confirmation", "planning_stage_entry",
            "technical_planning", "technical_planning_begin", "technical_planning_generate",
            "technical_planning_commit", "technical_planning_confirm", "template_reconcile",
        )
        with patch("app.graph.application_planning_workflow.load_application_lifecycle",
                   side_effect=AssertionError("重试入口不应读取 Lifecycle")):
            for node in nodes:
                with self.subTest(node=node):
                    result = _route_start({"workspace": "/unused", "resume_from": node,
                                           "workflow_action": "retry_template_reconcile"})
                    self.assertEqual(result, "technical_planning_begin" if node == "technical_planning" else node)

    async def test_only_explicit_continue_admits_current_stopped_owner(self):
        """暂停执行仅由 continue 接纳；已结束、换 owner 或普通失败重试不能借此重启。"""

        from app.domain.execution_recovery import DurableExecutionStatus, RecoveryExecutionError
        from app.services.business_node_retry import _stopped_workbench_source
        with tempfile.TemporaryDirectory() as workspace:
            source = self.source(workspace).model_copy(update={"status": DurableExecutionStatus.CANCELLED})
            execution = SimpleNamespace(status="stopped", thread_id=source.thread_id, owner_session_id=source.owner_session_id)
            lifecycle = SimpleNamespace(active_executions={source.run_id: execution})
            with patch("app.services.application_lifecycle.load_application_lifecycle", return_value=lifecycle):
                self.assertTrue(_stopped_workbench_source(source))
                with self.assertRaises(RecoveryExecutionError) as denied:
                    await prepare_business_node_retry(source, object())
                self.assertEqual(denied.exception.code, "RETRY_SOURCE_ACTIVE")
                with patch("app.services.business_node_retry.read_retry_hint", AsyncMock(return_value=None)), patch("app.services.business_node_retry.release_unretryable_execution", AsyncMock()) as release:
                    with self.assertRaises(RecoveryExecutionError) as missing:
                        await prepare_business_node_retry(source, object(), allow_stopped=True)
                    self.assertEqual(missing.exception.code, "RETRY_ENTRY_NOT_FOUND")
                    release.assert_awaited_once_with(source)
                execution.owner_session_id = "other"
                self.assertFalse(_stopped_workbench_source(source))
                lifecycle.active_executions = {}
                self.assertFalse(_stopped_workbench_source(source))

    def test_unit_test_reentry_collects_disk_module_files_without_old_build_state(self):
        """新 State 没有 Build 变更集时仍从阶段读取的当前模块文件生成单测目标。"""

        from app.graph.subgraphs.testing import collect_unit_test_targets
        with tempfile.TemporaryDirectory() as workspace:
            source = Path(workspace) / "frontend/src/pages/RetryCheck/index.tsx"
            source.parent.mkdir(parents=True)
            source.write_text("export default function Page() { return <div>current disk</div> }")
            state = {"workspace": workspace, "retry_source_run_id": "failed-unit-run"}
            with patch("app.services.development_review_files.development_review_files", return_value=["frontend/src/pages/RetryCheck/index.tsx"]), patch("app.graph.nodes.workspace_inspection.inspect_workspace", return_value={}):
                fields = load_stage_artifacts(state, "unit_test")
            result = collect_unit_test_targets({**state, **fields})
            context = result["unit_test_generation_context"]
            self.assertTrue(context["has_targets"])
            self.assertEqual(context["source_files"], ["frontend/src/pages/RetryCheck/index.tsx"])
            self.assertEqual(context["affected_layers"], ["frontend"])

    async def test_disk_loading_preserves_confirmation_command_route(self):
        """正式确认返回 Command 时仍能提交资产并沿原路由推进。"""
        from app.graph.state import ProjectState
        with tempfile.TemporaryDirectory() as workspace:
            self.write_json(workspace, "drafts/specs/requirement-spec.json", {"value": "draft"})

            def review(state):
                """模拟确认门已接受显式确认后发出的业务路由。"""
                self.assertEqual(state["requirement_spec"], {"value": "draft"})
                return Command(update={"request": "confirmed"}, goto="next_stage")

            def next_stage(state):
                """确认路由和本次磁盘资产都到达下游。"""
                self.assertEqual(state["request"], "confirmed")
                self.assertEqual(state["requirement_spec"], {"value": "draft"})
                return {"status": "completed"}

            builder = ArtifactStateGraph(ProjectState)
            builder.add_node("requirements", review)
            builder.add_node("next_stage", next_stage)
            builder.add_edge(START, "requirements")
            builder.add_edge("next_stage", END)
            result = await builder.compile().ainvoke({"workspace": workspace})
            self.assertEqual(result["status"], "completed")

    def source(self, workspace):
        """构造精确属于当前测试会话的失败记录。"""
        return DurableExecutionRecord(run_id="source", thread_id="thread", owner_session_id="session",
            workspace=workspace, execution_kind="workbench", first_node="build", current_node="build",
            status="failed", started_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))

    def write_json(self, workspace, relative, value):
        """写入当前合同位置的产物。"""
        path = Path(workspace, ".devagentstudio", relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    async def test_old_planning_retry_cannot_replace_new_owner(self):
        """旧 checkpoint 即使仍存在，也不能把新会话的运行归属抢回去。"""
        from app.services.application_lifecycle import write_application_lifecycle
        from app.domain.execution_recovery import RecoveryExecutionError
        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            current = load_application_lifecycle(workspace)
            write_application_lifecycle(workspace, current.model_copy(update={"active_run_id": "new-owner"}))
            source = self.source(workspace).model_copy(update={"execution_kind": "application_planning"})
            with self.assertRaises(RecoveryExecutionError):
                await prepare_business_node_retry(source, object())
            self.assertEqual(load_application_lifecycle(workspace).active_run_id, "new-owner")

    async def test_stage_reads_disk_each_time_and_does_not_keep_missing_assets(self):
        """需求草稿随磁盘更新，正式下游不借用草稿或旧 State。"""
        with tempfile.TemporaryDirectory() as workspace:
            self.write_json(workspace, "specs/requirement-spec.json", {"value": "formal"})
            self.write_json(workspace, "drafts/specs/requirement-spec.json", {"value": "draft"})
            state = {"workspace": workspace, "requirement_spec": {"value": "stale"}, "technical_plan": {"old": True}}
            self.assertEqual(load_stage_artifacts(state, "requirements")["requirement_spec"]["value"], "draft")
            self.assertEqual(load_stage_artifacts(state, "ui_confirmation")["requirement_spec"]["value"], "formal")
            self.assertEqual(load_stage_artifacts(state, "ui_confirmation")["technical_plan"], {})
            self.write_json(workspace, "specs/requirement-spec.json", {"value": "edited"})
            self.assertEqual(load_stage_artifacts(state, "ui_confirmation")["requirement_spec"]["value"], "edited")

    async def test_fresh_retry_graph_discards_checkpoint_state_and_reads_current_files(self):
        """同 Thread 新执行从 START 重进节点，旧确认和旧产物不会复活。"""
        from app.graph.state import ProjectState
        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            start_workbench_execution(workspace, scope="page", target_id="page-a", page_id="page-a",
                thread_id="thread", run_id="source", phase="build", owner_session_id="session")
            fail_workflow_lifecycle(workspace, run_id="source", phase="build", error=RuntimeError("failed"))

            received = []

            def build(state):
                """记录真实节点输入，不调用模型或写业务代码。"""
                received.append(state)
                if len(received) == 1:
                    raise RuntimeError("original Build interrupted")
                return {"status": "completed"}

            builder = ArtifactStateGraph(ProjectState)
            builder.add_node("build", build)
            builder.add_edge(START, "build")
            builder.add_edge("build", END)
            graph = builder.compile(checkpointer=InMemorySaver())
            with self.assertRaises(RuntimeError):
                await graph.ainvoke({"active_run_id": "source", "request": "保留上次提问", "clarification": {"old": True}},
                    {"configurable": {"thread_id": "thread"}})
            context = await prepare_business_node_retry(self.source(workspace), graph)
            try:
                self.write_json(workspace, "specs/requirement-spec.json", {"from": "current disk"})
                await graph.ainvoke(context.fork_snapshot.values, context.fork_config)
                self.assertEqual(received[-1]["request"], "保留上次提问")
                self.assertNotIn("clarification", received[-1])
                self.assertEqual(received[-1]["requirement_spec"], {"from": "current disk"})
                self.assertNotEqual(context.new_run_id, "source")
            finally:
                if context.workspace_lease:
                    context.workspace_lease.release()

    async def test_missing_checkpoint_is_notice_and_releases_only_source(self):
        """缺失入口没有重试动作，释放旧占用后其他会话仍可启动。"""
        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            start_workbench_execution(workspace, scope="page", target_id="page-a", page_id="page-a",
                thread_id="thread", run_id="source", phase="build", owner_session_id="session")
            start_workbench_execution(workspace, scope="page", target_id="page-b", page_id="page-b",
                thread_id="other", run_id="other", phase="build", owner_session_id="other")
            fail_workflow_lifecycle(workspace, run_id="source", phase="build", error=RuntimeError("failed"))

            from app.workspace.run_lease import workspace_run_leases
            source_lease = workspace_run_leases.acquire(workspace_root=workspace, project_id=None, thread_id="thread", run_id="source")
            other_lease = workspace_run_leases.acquire(workspace_root=workspace, project_id=None, thread_id="other", run_id="other")

            class MissingGraph:
                """模拟存储读取成功，但确实没有提交 checkpoint。"""
                nodes = {"build": None}

                async def aget_state_history(self, config):
                    """返回空历史，区别于需要保留入口的存储故障。"""
                    if False:
                        yield None

            source = self.source(workspace)
            plan = await retry_action_plan(source, MissingGraph())
            self.assertEqual(plan.reason_code, "RETRY_ENTRY_NOT_FOUND")
            self.assertIsNone(plan.primary_action)
            await release_unretryable_execution(source)
            current = load_application_lifecycle(workspace)
            self.assertNotIn("source", current.active_executions)
            self.assertIn("other", current.active_executions)
            self.assertEqual(workspace_run_leases.active_owner(workspace_root=workspace, project_id=None).run_id, "other")
            source_lease.release()
            other_lease.release()
            start_workbench_execution(workspace, scope="page", target_id="page-a", page_id="page-a",
                thread_id="new-thread", run_id="new", phase="build", owner_session_id="new-session")

    async def test_hint_does_not_copy_any_checkpoint_payload(self):
        """入口提取仅返回节点与提问，损坏的旧业务状态不参与安全恢复判断。"""
        class HintGraph:
            """提供无精确 Node Entry 的最小 checkpoint 观察。"""
            nodes = {"build": None}

            async def aget_state_history(self, config):
                """返回包含无效旧状态、但有明确节点和原提问的快照。"""
                yield SimpleNamespace(values={"active_run_id": "source", "request": "原提问",
                    "application_planning_interaction": {"action": "confirm", "request": "正确，继续规划"},
                    "technical_plan": "corrupt", "lifecycle": "stale"}, next=("build",), tasks=())

        hint = await read_retry_hint(self.source("/unused"), HintGraph())
        self.assertEqual(vars(hint), {"node": "build", "request": "原提问"})
