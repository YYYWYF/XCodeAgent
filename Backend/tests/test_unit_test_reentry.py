"""单测节点精确重入、进度归属及修复额度回归。"""

import unittest
from unittest.mock import patch

from app.domain.execution_recovery import RecoveryExecutionError, WorkflowReentryReason
from app.services.node_recovery_context import NodeRecoveryContext, bind_node_recovery, bind_recovery_runtime
from app.services.unit_test_reentry import prepare_unit_test_reentry


class UnitTestReentryTests(unittest.TestCase):
    """验证重入保留可证明进度，未提交任务从节点入口重新执行。"""

    def prepare(self, node, progress=None):
        """在真实节点上下文内准备重入状态，隔离工作区扫描。"""
        state = {
            "active_run_id": "child", "active_thread_id": "thread",
            "build_execution_scope": {"type": "page", "targetId": "page"},
            "unit_test_decision": "run", "unit_test_gate_passed": True,
            "unit_test_repair_attempts": {"backend_unit_tests": 4},
            "repair_tasks": [{"id": "a", "status": "pending"}],
        }
        if progress is not None:
            progress = {**state, **progress}
        context = NodeRecoveryContext(
            source_run_id="source", execution_run_id="child", thread_id="thread",
            target_node=node, checkpoint_id="entry",
            reentry_reason=WorkflowReentryReason.FAILURE_RETRY if progress else WorkflowReentryReason.INTERRUPTED_CONTINUE,
            entry_state=state, internal_progress=progress,
        )
        with bind_recovery_runtime(context), bind_node_recovery(state, node), patch(
            "app.graph.nodes.workspace_inspection.inspect_workspace",
            return_value={"workspace_revision": "current", "workspace_snapshot_path": "current.json"},
        ):
            return prepare_unit_test_reentry(state, node)

    def test_interrupted_unit_restarts_with_current_files_and_preserves_decision(self):
        """中断回到单测入口，刷新事实且撤销旧通过标记。"""
        state, fields = self.prepare("unit_test")
        self.assertEqual(state["unit_test_decision"], "run")
        self.assertEqual(state["workspace_revision"], "current")
        self.assertFalse(state["unit_test_gate_passed"])
        self.assertEqual(state["unit_test_repair_attempts"]["backend_unit_tests"], 4)
        self.assertEqual(fields["workspace_snapshot_path"], "current.json")

    def test_failed_repair_keeps_completed_tasks_and_recharges_only_next_dispatch(self):
        """同入口失败进度保留完成任务，未完成任务重派发但不归零计费。"""
        state, _ = self.prepare("unit_test_repair", {
            "small_task_tasks": [{"id": "a", "status": "completed"}, {"id": "b", "status": "failed"}],
            "unit_test_repair_attempts": {"backend_unit_tests": 6},
            "unit_test_repair_charged_checks": ["backend_unit_tests"],
        })
        self.assertEqual([t["status"] for t in state["small_task_tasks"]], ["completed", "pending"])
        self.assertEqual(state["unit_test_repair_attempts"]["backend_unit_tests"], 6)
        self.assertEqual(state["unit_test_repair_charged_checks"], [])

    def test_interrupted_repair_without_internal_progress_restarts_entry_tasks(self):
        """修复中断没有持久化内部进度时，从入口的 pending 任务开始。"""
        state, _ = self.prepare("unit_test_repair")
        self.assertEqual(state["small_task_tasks"], [{"id": "a", "status": "pending"}])

    def test_unrelated_progress_is_rejected(self):
        """不允许另一个目标的单测进度覆盖已验证入口。"""
        with self.assertRaises(RecoveryExecutionError):
            self.prepare("unit_test", {"build_execution_scope": {"type": "application"}})

    def test_normal_call_does_not_reset_or_scan(self):
        """正常确认及修复循环沿用现有逻辑，不触发外层重入处理。"""
        state = {"unit_test_gate_passed": True}
        self.assertEqual(prepare_unit_test_reentry(state, "unit_test"), (state, {}))
