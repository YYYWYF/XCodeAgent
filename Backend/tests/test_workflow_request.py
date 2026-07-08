from __future__ import annotations

import unittest

from app.protocols.workflow_request import workflow_run_inputs


class WorkflowRequestTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
