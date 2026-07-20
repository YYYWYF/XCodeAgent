from __future__ import annotations

import unittest

from app.protocols.workflow.projection import (
    _workflow_next_nodes,
    _workflow_summary,
)


class WorkflowProjectionTests(unittest.TestCase):
    def test_integration_repair_next_nodes_match_runtime_route(self) -> None:
        """验证可视化预测与实际修复任务路由保持一致。"""

        self.assertEqual(
            _workflow_next_nodes(
                "integration_test",
                {"quality_gate_passed": False, "integration_next_action": "repair_build"},
            ),
            ["build"],
        )

    def test_failed_summary_explains_exhausted_budget_without_stale_preview(self) -> None:
        """验证失败摘要展示修复计数和终止原因，并隐藏旧预览地址。"""

        summary = _workflow_summary(
            {
                "phase": "failed",
                "status": "failed",
                "quality_gate_passed": False,
                "repair_iteration": 3,
                "max_repair_iterations": 3,
                "preview_url": "http://127.0.0.1:3000",
                "repair_task_plan": {
                    "status": "terminal_failure",
                    "reason": "Integration repair iteration budget exhausted.",
                },
            },
            [],
        )

        self.assertIn("修复次数=3/3", summary["message"])
        self.assertIn("Integration repair iteration budget exhausted.", summary["message"])
        self.assertNotIn("预览地址", summary["message"])


if __name__ == "__main__":
    unittest.main()
