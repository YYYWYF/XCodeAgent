from __future__ import annotations

import unittest

from app.protocols.workflow_request import workflow_run_inputs


class WorkflowRequestTests(unittest.TestCase):
    def test_reads_workspace_root_from_forwarded_props(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "创建一个库存管理系统",
                "forwardedProps": {
                    "workspaceRoot": "/Users/sbw/Downloads/test/manage",
                },
            }
        )

        self.assertEqual(inputs["workspace"], "/Users/sbw/Downloads/test/manage")

    def test_reads_cancel_run_id_from_forwarded_props(self) -> None:
        inputs = workflow_run_inputs(
            {"forwardedProps": {"cancelRunId": "workflow-active-run"}}
        )

        self.assertEqual(inputs["cancel_run_id"], "workflow-active-run")

    def test_merges_clarification_answers_with_original_request(self) -> None:
        inputs = workflow_run_inputs(
            {
                "originalRequest": "帮我做一个库房系统",
                "clarificationAnswers": [
                    {
                        "question": "系统有哪些用户角色？",
                        "answer": ["普通员工", "库管员"],
                    },
                    {
                        "question": "核心功能有哪些？",
                        "answer": "入库管理、出库管理、库存查询",
                    },
                ],
            }
        )

        request = inputs["request"]
        self.assertIn("原始需求", request)
        self.assertIn("帮我做一个库房系统", request)
        self.assertIn("系统有哪些用户角色", request)
        self.assertIn("普通员工、库管员", request)
        self.assertIn("入库管理、出库管理、库存查询", request)

    def test_infers_resume_from_forwarded_resume_state(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "补充后的需求",
                "forwardedProps": {
                    "resumeState": {
                        "events": [
                            {
                                "type": "workflow.node.completed",
                                "nodeName": "requirements",
                                "status": "requires_user_input",
                            }
                        ]
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "requirements")

    def test_clarification_answers_default_to_requirements_resume(self) -> None:
        inputs = workflow_run_inputs(
            {
                "originalRequest": "帮我做一个库房系统",
                "clarificationAnswers": {"用户角色": ["库管员"]},
            }
        )

        self.assertEqual(inputs["resume_from"], "requirements")
        self.assertNotIn("原始需求：\n请基于原始需求", inputs["request"])
        self.assertIn("回答：库管员", inputs["request"])

    def test_merges_other_choice_input_as_a_requirement_supplement(self) -> None:
        inputs = workflow_run_inputs(
            {
                "originalRequest": "创建库存管理系统",
                "clarificationAnswers": {
                    "库存页面": {
                        "selected": ["库存列表"],
                        "other": "列表必须支持按仓库分组并导出 Excel",
                    }
                },
            }
        )

        self.assertIn("已选：库存列表", inputs["request"])
        self.assertIn("其他补充：列表必须支持按仓库分组并导出 Excel", inputs["request"])
        self.assertNotIn("__other__", inputs["request"])

    def test_infers_detail_confirmation_resume_and_preserves_plan_state(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "我选择 页面：库存管理列表页",
                "forwardedProps": {
                    "resumeState": {
                        "events": [
                            {
                                "type": "workflow.node.completed",
                                "node": {"id": "detail_confirmation"},
                                "status": "requires_user_input",
                            }
                        ],
                        "result": {
                            "project_plan": {"frontend_pages": []},
                            "project_plan_path": "var/plans/project-plan.md",
                            "page_spec_draft": {"page_id": "inventory_page"},
                        },
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "detail_confirmation")
        self.assertEqual(inputs["resume_values"]["project_plan"], {"frontend_pages": []})
        self.assertEqual(
            inputs["resume_values"]["page_spec_draft"],
            {"page_id": "inventory_page"},
        )

    def test_extracts_structured_batch_detail_review_submission(self) -> None:
        submission = {
            "review_status": "confirmed",
            "target_changes": [
                {
                    "target_type": "page",
                    "target_id": "inventory_page",
                    "changes": {"interactions": ["搜索", "导出"]},
                }
            ],
        }
        inputs = workflow_run_inputs(
            {
                "clarificationAnswers": {"detail_review": submission},
                "forwardedProps": {
                    "resumeState": {
                        "events": [
                            {
                                "type": "workflow.node.completed",
                                "node": {"id": "detail_confirmation"},
                                "status": "requires_user_input",
                            }
                        ],
                        "result": {
                            "pending_project_plan": {"confirmation_status": "pending_user_confirmation"}
                        },
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "detail_confirmation")
        self.assertEqual(
            inputs["resume_values"]["detail_review_submission"],
            submission,
        )

    def test_infers_project_planning_resume_and_preserves_plan_state(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "正确，继续",
                "forwardedProps": {
                    "resumeState": {
                        "events": [
                            {
                                "type": "workflow.node.completed",
                                "node": {"id": "project_planning"},
                                "status": "requires_user_input",
                            }
                        ],
                        "result": {
                            "requirement_spec": {"version": "0.1.0"},
                            "project_plan": {"version": "0.1.0"},
                        },
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "project_planning")
        self.assertEqual(inputs["resume_values"]["project_plan"], {"version": "0.1.0"})
        self.assertEqual(
            inputs["resume_values"]["requirement_spec"],
            {"version": "0.1.0"},
        )

    def test_infers_prepare_build_tasks_resume_for_plan_confirmation_guard(self) -> None:
        inputs = workflow_run_inputs(
            {
                "request": "正确，继续",
                "forwardedProps": {
                    "resumeState": {
                        "events": [
                            {
                                "type": "workflow.node.completed",
                                "node": {"id": "prepare_build_tasks"},
                                "status": "requires_user_input",
                            }
                        ],
                        "result": {
                            "project_plan": {
                                "version": "0.1.0",
                                "confirmation_status": "pending_user_confirmation",
                            },
                        },
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "prepare_build_tasks")
        self.assertEqual(
            inputs["resume_values"]["project_plan"]["confirmation_status"],
            "pending_user_confirmation",
        )


if __name__ == "__main__":
    unittest.main()
