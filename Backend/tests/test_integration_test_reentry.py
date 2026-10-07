"""集成检查和修复重入的工作区刷新、额度及来源归属回归。"""

import unittest
from unittest.mock import patch

from app.domain.execution_recovery import RecoveryExecutionError, WorkflowReentryReason
from app.services.integration_test_reentry import prepare_integration_test_reentry
from app.services.node_recovery_context import NodeRecoveryContext, bind_node_recovery, bind_recovery_runtime


class IntegrationTestReentryTests(unittest.TestCase):
    """确保外层兜底重入不会重置业务预算或带回单测。"""

    def prepare(self, node, progress=None):
        """提供真实恢复上下文并替换耗时的代码图扫描。"""

        state = {
            "active_run_id": "child", "active_thread_id": "thread",
            "build_execution_scope": {"type": "endpoint", "targetId": "api"},
            "unit_test_decision": "skip", "repair_iteration": 2, "max_repair_iterations": 3,
            "quality_gate_passed": True, "integration_build_checks_completed": True,
            "integration_build_results": [{"passed": True}],
        }
        context = NodeRecoveryContext(
            source_run_id="source", execution_run_id="child", thread_id="thread",
            target_node=node, checkpoint_id="entry", reentry_reason=WorkflowReentryReason.INTERRUPTED_CONTINUE,
            entry_state=state, internal_progress={**state, **progress} if progress else None,
        )
        with bind_recovery_runtime(context), bind_node_recovery(state, node), patch(
            "app.graph.nodes.workspace_inspection.inspect_workspace", return_value={"workspace_revision": "current"},
        ):
            return prepare_integration_test_reentry(state, node)

    def test_checks_rerun_preserving_skip_and_budget(self):
        """当前磁盘复测不复用旧成功结果，不重开单测或清空额度。"""

        state, fields = self.prepare("integration_test")
        self.assertEqual(state["workspace_revision"], "current")
        self.assertEqual(fields["workspace_revision"], "current")
        self.assertEqual(state["unit_test_decision"], "skip")
        self.assertEqual(state["repair_iteration"], 2)
        self.assertFalse(state["quality_gate_passed"])
        self.assertFalse(state["integration_build_checks_completed"])
        self.assertEqual(state["integration_build_results"], [])

    def test_repair_preserves_completed_tasks_and_returns_to_integration(self):
        """失败任务重新派发，已完成任务保留，返回原集成测试循环。"""

        state, _ = self.prepare("small_task_repair", {
            "repair_iteration": 3, "small_task_tasks": [{"id": "a", "status": "completed"}, {"id": "b", "status": "failed"}],
        })
        self.assertEqual([t["status"] for t in state["small_task_tasks"]], ["completed", "pending"])
        self.assertEqual(state["repair_return_node"], "integration_test")
        self.assertEqual(state["repair_iteration"], 3)

    def test_different_binding_is_rejected(self):
        """其他目标的内部进度不能污染本次修复。"""

        with self.assertRaises(RecoveryExecutionError):
            self.prepare("integration_test", {"build_execution_scope": {"type": "application"}})

    def test_normal_loop_does_not_reset(self):
        """普通用户确认及内部循环不触发外层恢复处理。"""

        state = {"quality_gate_passed": True}
        self.assertEqual(prepare_integration_test_reentry(state, "integration_test"), (state, {}))
