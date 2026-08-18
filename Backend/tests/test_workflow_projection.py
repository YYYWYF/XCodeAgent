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
                {"quality_gate_passed": False, "integration_next_action": "small_task_repair"},
            ),
            ["small_task_repair"],
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

    def test_application_planning_summary_omits_unrun_quality_gate(self) -> None:
        """创建规划没有集成测试结果时不得把缺失值显示成质量门禁未通过。"""

        summary = _workflow_summary(
            {
                "phase": "project_planning",
                "status": "completed",
                "workflow_scope": "application_planning",
            },
            [],
        )

        self.assertNotIn("质量门禁", summary["message"])
        self.assertIsNone(summary["qualityGatePassed"])

    def test_main_workflow_summary_keeps_explicit_failed_quality_gate(self) -> None:
        """主 Workflow 明确返回 False 时仍应展示质量门禁未通过。"""

        summary = _workflow_summary(
            {
                "phase": "integration_test",
                "status": "failed",
                "quality_gate_passed": False,
            },
            [],
        )

        self.assertIn("质量门禁=未通过", summary["message"])

    def test_acceptance_summary_describes_preview_without_fake_questions(self) -> None:
        """验证预览验收不再被描述成等待补充零个问题。"""

        summary = _workflow_summary(
            {
                "phase": "launch_project",
                "status": "requires_user_input",
                "preview_url": "http://127.0.0.1:3000",
                "acceptance_request": {"status": "requires_user_input"},
                "clarification": {
                    "mode": "page_acceptance",
                    "status": "requires_user_input",
                },
            },
            [],
        )

        self.assertIn("项目预览已就绪", summary["message"])
        self.assertIn("http://127.0.0.1:3000", summary["message"])
        self.assertNotIn("Workflow", summary["message"])
        self.assertNotIn("待确认问题 0", summary["message"])

    def test_unit_test_confirmation_ignores_stale_preview_state(self) -> None:
        """重试集成测试时单测确认必须覆盖 checkpoint 中的旧预览提示。"""

        summary = _workflow_summary(
            {
                "phase": "integration_test",
                "status": "requires_user_input",
                "preview_url": "http://127.0.0.1:3000",
                "acceptance_request": {"status": "requires_user_input"},
                "clarification": {
                    "mode": "unit_test_confirmation",
                    "status": "requires_user_input",
                    "questions": [{"id": "unit_test_confirmation"}],
                },
            },
            [],
        )

        self.assertIn("构建检查已完成", summary["message"])
        self.assertNotIn("项目预览已就绪", summary["message"])
        self.assertNotIn("预览地址", summary["message"])
        self.assertIsNone(summary["previewUrl"])
        self.assertIsNone(summary["launchResult"])
        self.assertIsNone(summary["acceptanceRequest"])

    def test_clarification_summary_reports_real_question_count(self) -> None:
        """验证确有澄清问题时使用面向用户的数量提示。"""

        summary = _workflow_summary(
            {
                "phase": "requirements",
                "status": "requires_user_input",
                "clarification": {
                    "status": "requires_user_input",
                    "questions": [{"id": "role"}, {"id": "scope"}],
                },
            },
            [],
        )

        self.assertEqual(summary["message"], "还有 2 个问题需要补充，完成后将继续执行。")

    def test_confirmation_summary_names_the_pending_artifact(self) -> None:
        """验证无问题的正式文档确认门禁显示具体待确认对象。"""

        summary = _workflow_summary(
            {
                "phase": "project_planning",
                "status": "requires_user_input",
                "clarification": {
                    "status": "requires_user_input",
                    "mode": "project_plan_confirmation",
                    "questions": [],
                },
            },
            [],
        )

        self.assertEqual(summary["message"], "项目计划已生成，请确认后继续。")

    def test_summary_projects_authoritative_lifecycle_without_deriving_phase(self) -> None:
        """AG-UI 摘要应原样投影 lifecycle，供前端直接消费业务阶段。"""

        lifecycle = {
            "schemaVersion": "1.2.0",
            "revision": 4,
            "initialization": {
                "stage": "awaiting_requirement_confirmation",
                "status": "awaiting_user",
            },
        }
        summary = _workflow_summary(
            {
                "phase": "requirements",
                "status": "requires_user_input",
                "clarification": {"status": "requires_user_input"},
                "lifecycle": lifecycle,
            },
            [],
        )

        self.assertIs(summary["lifecycle"], lifecycle)


if __name__ == "__main__":
    unittest.main()
