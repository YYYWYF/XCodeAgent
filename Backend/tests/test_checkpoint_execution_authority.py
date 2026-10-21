"""验证异步 checkpoint 为执行依据，以及缺前置文件时的明确重新生成出口。"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END
from langgraph.types import interrupt
from app.domain.execution_recovery import DurableExecutionStatus

from app.graph.state import ProjectState
from app.services.stage_artifacts import ArtifactStateGraph, load_stage_artifacts
from app.services.checkpoint_execution import read_checkpoint_execution, resolve_checkpoint_source
from app.services.business_node_retry import prepare_business_node_retry
from app.services.business_node_retry import read_retry_hint, retry_action_plan
from app.services.stage_prerequisites import StagePrerequisiteError, regeneration_for_retry
from app.services.application_lifecycle import load_application_lifecycle
from app.services.retry_preparation import abandon_prepared_retry
from tests.test_business_node_retry import BusinessNodeRetryTests
from tests.test_retry_preparation_failures import RetryPreparationFailureTests


class CheckpointAuthorityTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 Graph 区分业务 checkpoint、旧索引和进程存活事实。"""

    async def test_committed_request_before_workflow_entry_has_retry_action(self):
        """首业务节点尚未进入时，使用同一 checkpoint 已保存的入口及原请求。"""
        from app.services.workflow_reentry import workflow_entry
        with tempfile.TemporaryDirectory() as root:
            source = BusinessNodeRetryTests().source(root).model_copy(update={"status": DurableExecutionStatus.INTERRUPTED})
            builder = ArtifactStateGraph(ProjectState)
            builder.add_node("workflow_entry", workflow_entry)

            def build(state):
                """该业务节点尚不应被调用。"""
                raise AssertionError("仍停在业务入口之前")

            builder.add_node("build", build)
            builder.add_edge(START, "workflow_entry")
            builder.add_edge("workflow_entry", "build")
            builder.add_edge("build", END)
            graph = builder.compile(checkpointer=InMemorySaver(), interrupt_before=["workflow_entry"])
            await graph.ainvoke({"active_run_id": "source", "request": "开发当前页面", "resume_from": "build"},
                               {"configurable": {"thread_id": "thread"}})
            plan = await retry_action_plan(source, graph)
            self.assertEqual(plan.primary_action.target_node, "build")
            self.assertEqual((await read_retry_hint(source, graph)).request, "开发当前页面")

    async def test_sqlite_current_failure_does_not_decode_whole_history(self):
        """生产同款 SQLite 的当前现场足够时，不扫描整段历史也能取回节点和提问。"""
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        def build(state):
            """创建真实失败写入，原提问仅由 checkpoint 保存。"""
            raise RuntimeError("真实 SQLite 中断")

        with tempfile.TemporaryDirectory() as root:
            source = BusinessNodeRetryTests().source(root)
            async with AsyncSqliteSaver.from_conn_string(str(Path(root, "checkpoint.sqlite"))) as saver:
                builder = ArtifactStateGraph(ProjectState)
                builder.add_node("build", build)
                builder.add_edge(START, "build")
                builder.add_edge("build", END)
                graph = builder.compile(checkpointer=saver)
                with self.assertRaisesRegex(RuntimeError, "SQLite"):
                    await graph.ainvoke({"active_run_id": source.run_id, "request": "原始提问 SQLITE"},
                        {"configurable": {"thread_id": source.thread_id}}, durability="async")
                with patch.object(graph, "aget_state_history", side_effect=AssertionError("不应读取整个历史")):
                    hint = await read_retry_hint(source, graph)
                self.assertEqual((hint.node, hint.request), ("build", "原始提问 SQLITE"))

    async def test_checkpoint_io_error_is_not_a_missing_entry(self):
        """存储故障必须保留可重试错误，不能变成不存在的节点。"""
        from app.domain.execution_recovery import RecoveryExecutionError
        with tempfile.TemporaryDirectory() as root:
            source, graph, _ = await RetryPreparationFailureTests().fixture(root)
            with patch.object(graph, "aget_state", AsyncMock(side_effect=OSError("database busy"))):
                with self.assertRaises(RecoveryExecutionError) as caught:
                    await prepare_business_node_retry(source, graph)
            self.assertEqual(caught.exception.code, "RETRY_CHECKPOINT_UNAVAILABLE")
            self.assertIn(source.run_id, load_application_lifecycle(root).active_executions)

    async def test_checkpoint_failure_overrides_stale_waiting_and_node_mirror(self):
        """索引还在旧节点等待确认时，也能从 checkpoint 找到实际失败和原提问。"""
        with tempfile.TemporaryDirectory() as root:
            source, graph, _ = await RetryPreparationFailureTests().fixture(root)
            source = source.model_copy(update={"status": "awaiting_user", "current_node": "prepare_build_tasks"})
            execution = await resolve_checkpoint_source(source, graph)
            self.assertEqual(execution.status.value, "failed")
            self.assertEqual(execution.current_node, "build")
            hint = await read_retry_hint(execution, graph)
            self.assertEqual((hint.node, hint.request), ("build", "原始问题 ORIGINAL"))

    async def test_terminal_success_does_not_recover_older_failure(self):
        """最新已完成现场优先，不能从同 Run 的旧失败重新签发重试。"""
        with tempfile.TemporaryDirectory() as root:
            source, graph, _ = await RetryPreparationFailureTests().fixture(root)
            await graph.ainvoke(None, {"configurable": {"thread_id": source.thread_id}})
            execution = await resolve_checkpoint_source(source, graph)
            self.assertEqual(execution.status.value, "completed")
            self.assertIsNone(await read_retry_hint(source, graph))

    async def test_exit_without_error_record_uses_committed_pending_node(self):
        """只有执行前现场而没有异常写入，也能识别尚未完成的节点。"""
        source = BusinessNodeRetryTests().source("/unused").model_copy(update={"status": "running", "current_node": "old"})
        snapshot = SimpleNamespace(values={"active_run_id": source.run_id, "request": "原问题", "phase": "old", "status": "completed"},
                                   next=("build",), tasks=(), config={})

        async def history(config):
            """模拟退出前已经保存的唯一 checkpoint。"""
            yield snapshot

        graph = SimpleNamespace(nodes={"build": object()}, aget_state_history=history)
        with patch("app.services.checkpoint_execution.execution_is_live", return_value=False):
            execution = await read_checkpoint_execution(source, graph)
        self.assertEqual(execution.status.value, "interrupted")
        self.assertEqual((execution.node, execution.request), ("build", "原问题"))

    async def test_native_interrupt_overrides_failed_mirror(self):
        """实际已提交确认门时，不把旧失败索引当成新失败反复重执行。"""
        def review(state):
            """创建真实原生确认现场。"""
            interrupt({"message": "请确认当前文档"})
            return {"status": "completed"}

        graph_builder = ArtifactStateGraph(ProjectState)
        graph_builder.add_node("review", review)
        graph_builder.add_edge(START, "review")
        graph_builder.add_edge("review", END)
        graph = graph_builder.compile(checkpointer=InMemorySaver())
        source = BusinessNodeRetryTests().source("/unused")
        await graph.ainvoke({"active_run_id": source.run_id, "request": "原问题"},
                           {"configurable": {"thread_id": source.thread_id}})
        self.assertEqual((await resolve_checkpoint_source(source, graph)).status.value, "awaiting_user")

    async def test_planning_status_without_native_review_interrupt_is_retryable(self):
        """Planning 的 requires_user_input 投影没有原生审阅中断时应统一视为中断。"""
        source = BusinessNodeRetryTests().source("/unused").model_copy(update={
            "execution_kind": "application_planning",
            "workflow_scope": "application_planning",
            "status": DurableExecutionStatus.INTERRUPTED,
        })
        snapshot = SimpleNamespace(
            values={"active_run_id": source.run_id, "request": "恢复需求生成",
                    "phase": "requirements", "status": "requires_user_input"},
            next=("requirements",), tasks=(), config={},
        )
        graph = SimpleNamespace(
            nodes={"requirements": object(), "product_planning": object()},
            aget_state=AsyncMock(return_value=snapshot),
        )
        with patch("app.services.checkpoint_execution.execution_is_live", return_value=False):
            resolved = await resolve_checkpoint_source(source, graph)
            plan = await retry_action_plan(resolved, graph)
        self.assertEqual(resolved.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(plan.primary_action.target_node, "requirements")

    async def test_application_planning_native_review_interrupt_stays_awaiting_user(self):
        """真实规划审阅中断继续由用户确认，不被中断回退规则转换。"""
        source = BusinessNodeRetryTests().source("/unused").model_copy(update={
            "execution_kind": "application_planning",
            "workflow_scope": "application_planning",
            "status": DurableExecutionStatus.INTERRUPTED,
        })
        interrupt_item = SimpleNamespace(value={"type": "application_planning_review"}, id="review-id")
        task = SimpleNamespace(error=None, interrupts=(interrupt_item,))
        snapshot = SimpleNamespace(
            values={"active_run_id": source.run_id, "phase": "requirements_review",
                    "status": "requires_user_input"},
            next=("requirements_review",), tasks=(task,), config={},
        )
        graph = SimpleNamespace(nodes={"requirements_review": object()}, aget_state=AsyncMock(return_value=snapshot))
        with patch("app.services.checkpoint_execution.execution_is_live", return_value=False):
            resolved = await resolve_checkpoint_source(source, graph)
        self.assertEqual(resolved.status, DurableExecutionStatus.AWAITING_USER)

    async def test_empty_interrupted_planning_checkpoint_routes_to_unique_successor(self):
        """R02 这种无 next 的需求现场按保存状态确定地进入 ProductPlan。"""
        source = BusinessNodeRetryTests().source("/unused").model_copy(update={
            "execution_kind": "application_planning",
            "workflow_scope": "application_planning",
            "status": DurableExecutionStatus.INTERRUPTED,
        })
        snapshot = SimpleNamespace(
            values={"active_run_id": source.run_id, "request": "创建任务中心",
                    "phase": "requirements", "status": "clear",
                    "clarification": {"status": "clear"},
                    "requirement_spec": {"confirmation_status": "pending"},
                    "product_plan": {}},
            next=(), tasks=(), config={},
        )
        graph = SimpleNamespace(
            nodes={"requirements": object(), "requirements_review": object(), "product_planning": object()},
            aget_state=AsyncMock(return_value=snapshot),
        )
        with patch("app.services.checkpoint_execution.execution_is_live", return_value=False):
            execution = await read_checkpoint_execution(source, graph)
            plan = await retry_action_plan(source, graph)
        self.assertEqual(execution.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(execution.node, "product_planning")
        self.assertEqual(plan.primary_action.target_node, "product_planning")
        self.assertEqual(execution.request, "创建任务中心")

    async def test_ambiguous_planning_route_retries_last_business_node(self):
        """无法推导唯一规划后继时回到 checkpoint 中保存的最后业务节点。"""
        source = BusinessNodeRetryTests().source("/unused").model_copy(update={
            "execution_kind": "application_planning",
            "workflow_scope": "application_planning",
            "status": DurableExecutionStatus.INTERRUPTED,
        })
        snapshot = SimpleNamespace(
            values={"active_run_id": source.run_id, "request": "继续自定义阶段",
                    "phase": "custom_business", "status": "running"},
            next=(), tasks=(), config={},
        )
        graph = SimpleNamespace(nodes={"custom_business": object()}, aget_state=AsyncMock(return_value=snapshot))
        with patch("app.services.checkpoint_execution.execution_is_live", return_value=False):
            execution = await read_checkpoint_execution(source, graph)
        self.assertEqual(execution.node, "custom_business")


class ArtifactPrerequisiteRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """验证上游损坏提示、明确生成入口及跨 Graph 交接和撤销。"""

    def write(self, root, relative, value):
        """写入独立验收用当前产物，不修改真实应用。"""
        path = Path(root, ".devagentstudio", relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def formal_inputs(self, root):
        """声明正式应用并保留有效需求及产品输入，UI 故意缺失。"""
        self.write(root, "application.json", {"id": "app"})
        self.write(root, "specs/requirement-spec.json", {"requirement": "current"})
        self.write(root, "plans/product-plan.json", {"product": "current"})

    async def test_precise_prerequisite_notice_preserves_bad_bytes(self):
        """错误指出当前真正缺失的上游，损坏内容不被偷偷删除或当成通过。"""
        with tempfile.TemporaryDirectory() as root:
            self.formal_inputs(root)
            path = self.write(root, "specs/ui-designs.json", {})
            path.write_text("{broken")
            with self.assertRaises(StagePrerequisiteError) as caught:
                load_stage_artifacts({"workspace": root}, "technical_planning_begin")
            self.assertEqual(caught.exception.issue.node, "ui_confirmation")
            self.assertIn("ui-designs.json", caught.exception.issue.diagnostic)
            self.assertNotIn(".json", str(caught.exception))
            self.assertEqual(path.read_text(), "{broken")
            self.assertIsNone(regeneration_for_retry(root, "ui_confirmation"))

    async def test_build_missing_design_hands_off_and_waits_for_confirmation(self):
        """开发缺 UI 时一键交回已有 UI 节点，保留提问且移除旧工作台占用。"""
        with tempfile.TemporaryDirectory() as root:
            source, original_graph, _ = await RetryPreparationFailureTests().fixture(root)
            self.formal_inputs(root)
            before = load_application_lifecycle(root)
            seen = []

            def ui(state):
                """用真实 Graph 确认门验证交接，不调用 Agent 实现。"""
                seen.append(state)
                interrupt({"message": "请确认新 UI"})
                return {"status": "completed"}

            builder = ArtifactStateGraph(ProjectState)
            builder.add_node("ui_confirmation", ui)
            builder.add_edge(START, "ui_confirmation")
            builder.add_edge("ui_confirmation", END)
            planning = builder.compile(checkpointer=InMemorySaver())
            action = await retry_action_plan(source, original_graph)
            self.assertEqual(action.primary_action.label, "重新生成UI 设计")
            self.assertEqual(action.primary_action.target_node, "ui_confirmation")
            with patch("app.graph.application_planning_workflow.application_planning_graph_for_request", AsyncMock(return_value=planning)):
                context = await prepare_business_node_retry(source, original_graph)
            self.assertIs(context.graph, planning)
            self.assertEqual(context.workflow_scope, "application_planning")
            self.assertEqual(context.child_execution.execution_kind, "application_planning")
            self.assertNotIn(source.run_id, load_application_lifecycle(root).active_executions)
            try:
                chunks = [chunk async for chunk in context.graph.astream(context.fork_snapshot.values,
                    config=context.fork_config, stream_mode=["updates", "custom"])]
                self.assertIn("__interrupt__", str(chunks))
                self.assertEqual(seen[0]["request"], "原始问题 ORIGINAL")
                self.assertEqual(seen[0]["product_plan"], {"product": "current"})
                self.assertEqual(context.recovery_plan.target_node, "ui_confirmation")
            finally:
                await abandon_prepared_retry(workspace=root, source=source, before=before,
                    new_run_id=context.new_run_id, lease=context.workspace_lease)
            self.assertIn(source.run_id, load_application_lifecycle(root).active_executions)
            self.assertEqual(load_application_lifecycle(root).initialization, before.initialization)
