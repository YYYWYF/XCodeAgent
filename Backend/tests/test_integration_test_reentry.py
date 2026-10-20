"""集成检查和修复重入的工作区刷新、额度及来源归属回归。"""

import unittest
from unittest.mock import patch

from app.domain.execution_recovery import RecoveryExecutionError, WorkflowReentryReason
from app.services.integration_test_reentry import prepare_integration_test_reentry
from app.services.node_recovery_context import NodeRecoveryContext, bind_node_recovery, bind_recovery_runtime


class IntegrationTestReentryTests(unittest.TestCase):
    """确保显式失败重试重开预算，暂停续接及内部循环保持原规则。"""

    def prepare(self, node, progress=None, reason=WorkflowReentryReason.INTERRUPTED_CONTINUE):
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
            target_node=node, checkpoint_id="entry", reentry_reason=reason,
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

    def test_failure_restart_clears_tasks_and_budget_preserving_changes(self):
        """失败重试保留已落盘变更证据，但不复用旧任务、确认或性能选择。"""

        changes = [{"id": "repair-diff", "files": [{"path": "frontend/src/main.ts"}]}]
        for reason in (WorkflowReentryReason.FAILURE_RETRY, WorkflowReentryReason.BUSINESS_RETRY):
            with self.subTest(reason=reason):
                state, updates = self.prepare("integration_test", {
                    "repair_iteration": 3, "repair_tasks": [{"id": "old"}],
                    "repair_task_plan": {"decision": "repair"}, "small_task_tasks": [{"id": "old"}],
                    "small_task_handoff": {"mode": "small_task_scope_confirmation"},
                    "small_task_handoff_submission": {"decision": "approved"},
                    "frontend_performance_decision": "skip", "small_task_code_change_sets": changes,
                    "code_changes": {"id": "merged"},
                }, reason=reason)
                self.assertEqual(state["repair_iteration"], 0)
                self.assertEqual(updates["repair_iteration"], 0)
                self.assertEqual(state["max_repair_iterations"], 3)
                for key in ("repair_tasks", "small_task_tasks", "small_task_results"):
                    self.assertEqual(state[key], [])
                for key in ("repair_task_plan", "small_task_handoff", "small_task_handoff_submission", "clarification"):
                    self.assertEqual(state[key], {})
                self.assertEqual(state["frontend_performance_decision"], "")
                self.assertEqual(state["small_task_code_change_sets"], changes)
                self.assertEqual(state["code_changes"], {"id": "merged"})
                self.assertEqual(state["unit_test_decision"], "skip")

    def test_normal_loop_does_not_reset(self):
        """普通用户确认及内部循环不触发外层恢复处理。"""

        state = {"quality_gate_passed": True}
        self.assertEqual(prepare_integration_test_reentry(state, "integration_test"), (state, {}))
