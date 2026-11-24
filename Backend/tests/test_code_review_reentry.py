"""审查恢复的范围、确认权限、修复次数及工作区快照回归。"""

import unittest
from typing import Any
from unittest.mock import patch

from app.domain.execution_recovery import RecoveryExecutionError, WorkflowReentryReason
from app.services.code_review_reentry import prepare_code_review_reentry
from app.services.node_recovery_context import NodeRecoveryContext, bind_node_recovery, bind_recovery_runtime


class CodeReviewReentryTests(unittest.TestCase):
    """验证审查重入保留原授权和修复进度。"""

    def prepare(
        self, progress: dict[str, Any] | None = None, *, approved: bool = True,
    ) -> dict[str, Any]:
        """构造经验证的首次节点调用。"""

        state = {
            "active_run_id": "child", "active_thread_id": "thread", "code_review_mode": "diff",
            "build_execution_scope": {"type": "endpoint", "targetId": "api"},
            "code_review_repair_iteration": 2, "code_review_max_repair_iterations": 3,
            "code_review_repair_confirmation": {"action": "repair_all"} if approved else {},
            "code_review_result": {"issues": [{"id": "a"}]}, "workspace_revision": "old",
        }
        context = NodeRecoveryContext(source_run_id="source", execution_run_id="child", thread_id="thread",
                                      target_node="code_review", checkpoint_id="entry", reentry_reason=WorkflowReentryReason.INTERRUPTED_CONTINUE,
                                      entry_state=state, internal_progress={**state, **progress} if progress else None)
        with bind_recovery_runtime(context), bind_node_recovery(state, "code_review"):
            return prepare_code_review_reentry(state)

    def test_interrupted_diff_review_preserves_snapshot_and_confirmation(self) -> None:
        """后端中断续接沿用已有索引身份及原修复授权与额度。"""

        with patch("app.graph.nodes.workspace_inspection.inspect_workspace") as scan:
            state = self.prepare()
        scan.assert_not_called()
        self.assertEqual(state["workspace_revision"], "old")
        self.assertEqual(state["code_review_mode"], "diff")
        self.assertEqual(state["code_review_repair_iteration"], 2)
        self.assertEqual(state["code_review_repair_confirmation"]["action"], "repair_all")
        self.assertEqual(state["code_review_result"]["issues"], [{"id": "a"}])

    def test_failed_progress_keeps_consumed_iterations(self):
        """恢复提交的内部失败结果，不把消耗的修复轮次归零。"""

        state = self.prepare({"code_review_repair_iteration": 3, "code_review_build_results": [{"passed": False}]})
        self.assertEqual(state["code_review_repair_iteration"], 3)
        self.assertEqual(state["code_review_build_results"], [{"passed": False}])

    def test_progress_cannot_invent_user_confirmation(self):
        """未确认的一键修复不能从内部进度中复活为授权。"""

        state = self.prepare({"code_review_repair_confirmation": {"action": "repair_all"}}, approved=False)
        self.assertEqual(state["code_review_repair_confirmation"], {})

    def test_other_review_mode_or_scope_is_rejected(self):
        """另一个审查方式或目标的进度不能覆盖当前入口。"""

        for progress in ({"code_review_mode": "full"}, {"build_execution_scope": {"type": "application"}}):
            with self.subTest(progress=progress), self.assertRaises(RecoveryExecutionError):
                self.prepare(progress)

    def test_normal_call_does_not_scan_or_reset(self):
        """普通审查、确认及内部修复保持既有行为。"""

        state = {"code_review_repair_iteration": 2}
        self.assertIs(prepare_code_review_reentry(state), state)
