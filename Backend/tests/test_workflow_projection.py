from __future__ import annotations

import unittest

from app.protocols.workflow.projection import (
    _public_workflow_state,
    _workflow_next_nodes,
    _workflow_summary,
    _workflow_visual_payload,
)


class WorkflowProjectionTests(unittest.TestCase):
    def test_workspace_inspection_projects_direct_task_preparation(self) -> None:
        """工作区检查完成后不再投射独立数据库上下文节点。"""

        self.assertEqual(
            _workflow_next_nodes("inspect_workspace", {"status": "completed"}),
            ["prepare_build_tasks"],
        )

    def test_build_projects_unit_test_gate(self) -> None:
        """Build 成功后的可视化下一节点必须是开发阶段单测门禁。"""

        self.assertEqual(
            _workflow_next_nodes(
                "build", {"build_summary": {"status": "completed"}}
            ),
            ["unit_test"],
        )

        self.assertEqual(
            _workflow_next_nodes(
                "build",
                {"status": "failed", "build_summary": {"status": "completed"}},
            ),
            ["handle_failure"],
        )

    def test_test_phase_confirmation_projects_target(self) -> None:
        """测试阶段确认投影包含结构化目标摘要。"""

        summary = _workflow_summary(
            {
                "phase": "test_phase_confirmation",
                "status": "requires_user_input",
                "test_target": {"type": "endpoint", "id": "orders.list", "label": "GET /orders"},
                "clarification": {
                    "mode": "test_phase_confirmation",
                    "status": "requires_user_input",
                    "testTarget": {
                        "type": "endpoint",
                        "id": "orders.list",
                        "label": "GET /orders",
                    },
                },
            },
            [],
        )

        self.assertEqual(summary["testTarget"]["label"], "GET /orders")

    def test_integration_repair_next_nodes_match_runtime_route(self) -> None:
        """验证可视化预测与实际修复任务路由保持一致。"""

        self.assertEqual(
            _workflow_next_nodes(
                "integration_test",
                {"quality_gate_passed": False, "integration_next_action": "small_task_repair"},
            ),
            ["small_task_repair"],
        )

    def test_code_review_result_projects_nested_fields_to_camel_case(self) -> None:
        """审查结果的目标、问题和规则字段均使用 AG-UI camelCase。"""

        summary = _workflow_summary(
            {
                "phase": "code_review",
                "status": "completed",
                "code_review_result": {
                    "status": "completed",
                    "issue_count": 1,
                    "loaded_skills": ["frontend-code-scan", "backend-code-scan"],
                    "targets": [
                        {
                            "side": "frontend",
                            "root": "frontend/src",
                            "status": "completed",
                            "scanned_file_count": 3,
                        }
                    ],
                    "issues": [
                        {
                            "id": "issue-1",
                            "side": "frontend",
                            "rule_id": "FE001",
                            "severity": "high",
                            "title": "问题",
                            "summary": "说明",
                            "file": "frontend/src/App.tsx",
                            "line": 8,
                        }
                    ],
                },
            },
            [],
        )

        review = summary["codeReviewResult"]
        self.assertEqual(review["issueCount"], 1)
        self.assertEqual(review["targets"][0]["scannedFileCount"], 3)
        self.assertEqual(review["issues"][0]["ruleId"], "FE001")

    def test_integration_test_hides_stale_code_review_result(self) -> None:
        """测试重跑期间不得向测试 Agent 投影上一轮代码审查结果。"""

        result = {
            "phase": "integration_test",
            "status": "requires_user_input",
            "clarification": {
                "mode": "frontend_performance_confirmation",
                "status": "requires_user_input",
                "questions": [],
            },
            "code_review_result": {
                "status": "completed",
                "summary": "上一轮代码审查完成。",
                "issue_count": 0,
                "targets": [],
                "issues": [],
            },
        }
        summary = _workflow_summary(result, [])
        payload = _workflow_visual_payload(
            run_id="run-retest",
            thread_id="thread-test",
            summary=summary,
            events=[],
            result=result,
        )

        self.assertEqual(summary["codeReviewResult"], {})
        self.assertEqual(payload["state"]["codeReviewResult"], {})
        self.assertEqual(payload["result"]["codeReviewResult"], {})

    def test_code_review_repair_projects_status_and_build_checks(self) -> None:
        """代码审查修复状态和独立构建检查应持续投影为 camelCase。"""

        summary = _workflow_summary(
            {
                "phase": "code_review",
                "status": "in_progress",
                "code_review_result": {
                    "status": "completed",
                    "issue_count": 1,
                    "issues": [],
                },
                "code_review_repair_result": {
                    "status": "building",
                    "iteration": 1,
                    "max_iterations": 3,
                    "requested_issue_count": 1,
                    "attempted_issue_ids": ["CKR6002-1"],
                    "changed_files": ["backend/src/main/java/App.java"],
                    "build_checks": [
                        {
                            "id": "backend_build",
                            "name": "后端构建检查",
                            "layer": "backend",
                            "status": "running",
                        }
                    ],
                },
            },
            [],
        )

        repair = summary["codeReviewRepair"]
        self.assertEqual(repair["status"], "building")
        self.assertEqual(repair["iteration"], 1)
        self.assertEqual(repair["buildChecks"][0]["id"], "backend_build")
        self.assertEqual(repair["changedFiles"], ["backend/src/main/java/App.java"])

    def test_code_review_repair_preserves_skipped_build_check_status(self) -> None:
        """独立构建中未执行的可选检查应投影为 skipped 而不是 passed。"""

        summary = _workflow_summary(
            {
                "phase": "code_review",
                "status": "in_progress",
                "code_review_repair_result": {
                    "status": "building",
                    "build_checks": [
                        {
                            "id": "backend_build",
                            "name": "后端构建检查",
                            "layer": "backend",
                            "passed": True,
                            "skipped": True,
                        }
                    ],
                },
            },
            [],
        )

        self.assertEqual(
            summary["codeReviewRepair"]["buildChecks"][0]["status"],
            "skipped",
        )

    def test_code_review_build_logs_are_not_exposed_in_public_state(self) -> None:
        """公开状态只保留裁剪后的修复检查，不携带原始构建日志。"""

        summary = _workflow_summary(
            {
                "phase": "code_review",
                "status": "in_progress",
                "code_review_repair_result": {
                    "status": "building",
                    "iteration": 1,
                    "max_iterations": 3,
                    "build_checks": [
                        {
                            "id": "backend_build",
                            "name": "后端构建检查",
                            "passed": False,
                            "evidence": "failed at /Users/example/workspace/backend/src/main/java/App.java",
                        }
                    ],
                },
                "code_review_build_results": [
                    {
                        "id": "backend_build",
                        "execution": {"stdout": "full command output"},
                    }
                ],
                "code_review_events": ["review_build_checks"],
            },
            [],
        )

        public_state = _public_workflow_state(
            {
                "phase": "code_review",
                "code_review_build_results": [{"execution": {"stdout": "full command output"}}],
                "code_review_events": ["review_build_checks"],
                "code_review_repair_result": {
                    "status": "building",
                    "build_checks": [
                        {
                            "id": "backend_build",
                            "passed": False,
                            "evidence": "failed at /Users/example/workspace/backend/src/main/java/App.java",
                        }
                    ],
                },
            },
            phase="code_review",
        )
        self.assertNotIn("code_review_build_results", public_state)
        self.assertNotIn("code_review_events", public_state)
        self.assertEqual(summary["codeReviewRepair"]["buildChecks"][0]["status"], "failed")
        self.assertNotIn("/Users/example", summary["codeReviewRepair"]["buildChecks"][0]["evidence"])

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
            "revision": 4,
            "initialization": {
                "stage": "awaiting_requirement_document_confirmation",
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
