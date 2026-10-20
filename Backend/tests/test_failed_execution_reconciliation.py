"""失败事实优先于遗留确认，同时保留真实确认、其他任务和仍在退出的资源。"""

import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app.domain.application_lifecycle import PendingInteractionType
from app.domain.execution_recovery import DurableExecutionStatus
from app.services.application_lifecycle import (
    load_application_lifecycle, start_workbench_execution, update_workbench_execution,
)
from app.services.failed_execution_reconciliation import reconcile_failed_execution_lifecycle
from app.services.business_node_retry import prepare_business_node_retry
from app.services.execution_recovery import observe_execution_started, observe_execution_failed
from tests.test_business_node_retry import BusinessNodeRetryTests
from tests.test_checkpoint_first_retry import CheckpointFirstRetryTests
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class FailedExecutionReconciliationTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 Lifecycle、资源锁和 checkpoint 检查同步与重试的出口。"""

    def pending(self, root):
        """创建持有页面锁且仍残留确认的原工作台执行。"""
        _write_ready_workbench_lifecycle(root)
        start_workbench_execution(root, scope="page", target_id="page-a", page_id="page-a",
            thread_id="thread", run_id="source", phase="build", owner_session_id="session")
        update_workbench_execution(root, run_id="source", phase="test_phase_confirmation",
            status="awaiting_user", pending_type=PendingInteractionType.TEST_PHASE_CONFIRMATION,
            pending_payload={"mode": "test_phase_confirmation"})
        return BusinessNodeRetryTests().source(root)

    async def test_failed_pending_is_cleared_and_original_node_retries(self):
        """无需先确认无效 Pending，重试准备就能交接到固定原失败节点。"""
        with tempfile.TemporaryDirectory() as root:
            source = self.pending(root)

            def behavior(count, state):
                """原执行失败，重试使用原输入成功。"""
                if count == 1:
                    raise RuntimeError("original failure")
                return {"status": "completed"}

            graph, _, _, _, _, received = await CheckpointFirstRetryTests().fixture(root, behavior)
            context = await prepare_business_node_retry(source, graph)
            try:
                child = load_application_lifecycle(root).active_executions[context.new_run_id]
                self.assertIsNone(child.pending_interaction)
                self.assertEqual(child.status, "running")
                async for _ in context.graph.astream(context.fork_snapshot.values,
                        config=context.fork_config, stream_mode=["updates", "custom"]):
                    pass
                self.assertEqual(received[-1]["request"], "原始问题 ORIGINAL")
            finally:
                context.workspace_lease.release()

    def test_reconciliation_is_idempotent_and_preserves_other_owner(self):
        """只改失败来源的投影与锁，不抢占另一页面的 owner 或资源。"""
        with tempfile.TemporaryDirectory() as root:
            source = self.pending(root)
            start_workbench_execution(root, scope="page", target_id="page-b", page_id="page-b",
                thread_id="other-thread", run_id="other", phase="build", owner_session_id="other-session")
            update_workbench_execution(root, run_id="other", phase="review_phase_confirmation",
                status="awaiting_user", pending_type=PendingInteractionType.REVIEW_PHASE_CONFIRMATION)
            other = load_application_lifecycle(root).active_executions["other"]
            first = reconcile_failed_execution_lifecycle(source)
            self.assertEqual(first.active_executions["source"].status, "failed")
            self.assertIsNone(first.active_executions["source"].pending_interaction)
            self.assertNotIn("page-a", first.resource_locks.pages)
            self.assertEqual(first.resource_locks.pages["page-b"].run_id, "other")
            self.assertEqual(first.active_run_id, "other")
            self.assertEqual(first.active_executions["other"], other)
            self.assertEqual(reconcile_failed_execution_lifecycle(source).revision, first.revision)

    def test_real_waiting_confirmation_and_ended_source_are_untouched(self):
        """正常待确认不是失败；已经结束或不属于本 Thread 的执行不被重新创建。"""
        with tempfile.TemporaryDirectory() as root:
            source = self.pending(root)
            before = load_application_lifecycle(root)
            reconcile_failed_execution_lifecycle(source.model_copy(update={"status": DurableExecutionStatus.AWAITING_USER}))
            reconcile_failed_execution_lifecycle(source.model_copy(update={"thread_id": "other-thread"}))
            reconcile_failed_execution_lifecycle(source.model_copy(update={"run_id": "ended"}))
            self.assertEqual(load_application_lifecycle(root), before)

    def test_live_work_keeps_locks_until_actual_exit(self):
        """先修正失效确认投影，但真实外层运行未退出时不释放写入占用。"""
        with tempfile.TemporaryDirectory() as root:
            source = self.pending(root)
            with patch("app.protocols.workflow.run_control.workflow_run_registry.is_active", return_value=True):
                busy = reconcile_failed_execution_lifecycle(source)
                self.assertIsNone(busy.active_executions["source"].pending_interaction)
                self.assertEqual(busy.resource_locks.pages["page-a"].run_id, "source")
            self.assertNotIn("page-a", reconcile_failed_execution_lifecycle(source).resource_locks.pages)

    def test_interruption_before_first_node_keeps_readable_entry(self):
        """交接已登记但没有节点进度时，收口后仍能读回状态并交接重试。"""
        with tempfile.TemporaryDirectory() as root:
            source = self.pending(root).model_copy(update={
                "status": DurableExecutionStatus.INTERRUPTED,
                "current_node": None, "first_node": "api_design_readiness_gate",
            })
            reconciled = reconcile_failed_execution_lifecycle(source)
            current = load_application_lifecycle(root)
            self.assertEqual(current, reconciled)
            self.assertEqual(current.active_executions["source"].phase, "api_design_readiness_gate")
            self.assertIsNone(current.active_executions["source"].pending_interaction)
            self.assertNotIn("page-a", current.resource_locks.pages)

    def test_application_retry_does_not_expand_into_other_target_locks(self):
        """应用交接的恢复不额外占用已有页面确认，其他执行与锁保持原样。"""
        from app.services.application_lifecycle import handoff_workbench_execution_for_recovery
        with tempfile.TemporaryDirectory() as root:
            self.pending(root)
            start_workbench_execution(root, scope="application", target_id="application", page_id=None,
                thread_id="application-thread", run_id="application-run", phase="api_design_readiness_gate")
            before = load_application_lifecycle(root)
            source = BusinessNodeRetryTests().source(root).model_copy(update={
                "run_id": "application-run", "thread_id": "application-thread",
                "current_node": None, "first_node": "api_design_readiness_gate",
                "status": DurableExecutionStatus.INTERRUPTED,
            })
            ready = reconcile_failed_execution_lifecycle(source)
            child = handoff_workbench_execution_for_recovery(root, source_run_id=source.run_id,
                new_run_id="retry", thread_id=source.thread_id, phase=source.first_node,
                expected_lifecycle_revision=ready.revision)
            self.assertEqual(child.resource_locks.application.run_id, "retry")
            self.assertEqual(child.resource_locks.pages, before.resource_locks.pages)
            self.assertEqual(child.active_executions["source"], before.active_executions["source"])

    async def test_failure_submission_also_clears_old_confirmation(self):
        """生产失败提交即同步旧确认，不必等用户再次点击或读取。"""
        with tempfile.TemporaryDirectory() as root:
            self.pending(root)
            await observe_execution_started(workspace=root, project_id=None, thread_id="thread",
                run_id="source", workflow_scope="workflow", first_node="build", owner_session_id="session")
            await observe_execution_failed(workspace=root, run_id="source", thread_id="thread",
                workflow_scope="workflow", authoritative_node="build", exception=RuntimeError("failure"))
            state = load_application_lifecycle(root)
            self.assertEqual(state.active_executions["source"].status, "failed")
            self.assertIsNone(state.active_executions["source"].pending_interaction)

    async def test_recovery_projection_repairs_old_pending_before_returning_retry(self):
        """读取恢复入口会收敛旧确认，避免前端同步后继续展示不可提交的卡片。"""
        from app.services.business_node_retry import RetryHint
        from app.services.execution_recovery_projection import resolve_execution_recovery_projection
        with tempfile.TemporaryDirectory() as root:
            source = self.pending(root)
            with patch("app.services.execution_recovery_projection.list_recovery_projection_candidates", AsyncMock(return_value=[source])), \
                    patch("app.services.execution_recovery_projection.workflow_graph_for_request", AsyncMock(return_value=object())), \
                    patch("app.services.checkpoint_execution.resolve_checkpoint_source", AsyncMock(return_value=source)), \
                    patch("app.services.business_node_retry.read_retry_hint", AsyncMock(return_value=RetryHint("build", "原问题"))):
                projection = await resolve_execution_recovery_projection(root, active_workbench_run_ids={"source"})
            self.assertTrue(projection.candidates[0].can_continue)
            current = load_application_lifecycle(root)
            self.assertEqual(current.active_executions["source"].status, "failed")
            self.assertIsNone(current.active_executions["source"].pending_interaction)
