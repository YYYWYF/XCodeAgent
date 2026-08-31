from __future__ import annotations

import unittest

from app.graph.nodes.lifecycle import test_phase_confirmation
from app.graph.workflow import route_test_phase_confirmation
from app.protocols.workflow.projection import (
    _workflow_progress_summary,
    _workflow_summary,
    _workflow_visual_payload,
)
from app.protocols.workflow.request import _resume_values, workflow_run_inputs


class WorkflowTestPhaseConfirmationTests(unittest.TestCase):
    """覆盖 Build 状态恢复与测试阶段确认按钮契约。"""

    def test_empty_public_build_summary_does_not_override_completed_state(self) -> None:
        """公开结果中的空摘要不得覆盖 StateSnapshot 中的 Build 完成事实。"""

        values = _resume_values(
            {
                "state": {"buildSummary": {"status": "completed", "failed": 0}},
                "result": {"build_summary": {}},
            }
        )

        self.assertEqual(
            values["build_summary"],
            {"status": "completed", "failed": 0},
        )
        self.assertNotIn(
            "build_summary",
            _resume_values({"state": {"buildSummary": {}}}),
        )

    def test_missing_build_summary_is_omitted_from_public_projection(self) -> None:
        """增量状态未携带 Build 摘要时不得投影伪造的空对象。"""

        result = {"phase": "unit_test", "status": "completed"}
        progress = _workflow_progress_summary(result, [])
        summary = _workflow_summary(result, [])
        payload = _workflow_visual_payload(
            run_id="run-unit-test",
            thread_id="thread-development",
            summary=summary,
            events=[],
            result=result,
        )

        self.assertNotIn("buildSummary", progress)
        self.assertNotIn("buildSummary", summary)
        self.assertNotIn("buildSummary", payload["state"])

    def test_passing_unit_tests_reach_confirmation_button_state(self) -> None:
        """单测通过后必须进入待确认状态并返回前端按钮所需载荷。"""

        inputs = workflow_run_inputs(
            {
                "clarificationAnswers": {
                    "unit_test_confirmation": {"selected": "run"}
                },
                "resumeState": {
                    "summary": {
                        "status": "requires_user_input",
                        "phase": "unit_test",
                    },
                    "state": {
                        "buildSummary": {
                            "status": "completed",
                            "completed": 8,
                            "failed": 0,
                        }
                    },
                    "result": {"build_summary": {}},
                },
            }
        )
        confirmation = test_phase_confirmation(
            {
                **inputs["resume_values"],
                # 同 thread 恢复时该事实由服务端 checkpoint 合并，不由客户端回传。
                "build_summary": {
                    "status": "completed",
                    "completed": 8,
                    "failed": 0,
                },
                "application_name": "年龄录入",
                "unit_test_gate_passed": True,
                "build_execution_scope": {
                    "type": "page",
                    "targetId": "page_age_entry",
                    "targetLabel": "年龄录入页",
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "unit_test")
        self.assertNotIn("build_summary", inputs["resume_values"])
        self.assertEqual(confirmation["status"], "requires_user_input")
        self.assertEqual(
            confirmation["clarification"]["mode"],
            "test_phase_confirmation",
        )
        self.assertEqual(
            confirmation["clarification"]["testTarget"],
            {
                "type": "page",
                "id": "page_age_entry",
                "label": "年龄录入页",
            },
        )
        self.assertEqual(
            route_test_phase_confirmation(confirmation),
            "await_user_input",
        )

    def test_completed_server_tasks_recover_a_corrupted_build_summary(self) -> None:
        """旧 checkpoint 已被空摘要污染时应从服务端 Build 任务事实恢复确认卡。"""

        confirmation = test_phase_confirmation(
            {
                "build_summary": {},
                "tasks": [
                    {"id": "backend-service", "status": "completed"},
                    {"id": "frontend-page", "status": "already_satisfied"},
                ],
                "build_results": [
                    {"task_id": "backend-service", "status": "completed"},
                    {"task_id": "frontend-page", "status": "already_satisfied"},
                ],
                "unit_test_gate_passed": True,
                "application_name": "年龄录入",
            }
        )

        self.assertEqual(confirmation["status"], "requires_user_input")
        self.assertEqual(confirmation["build_summary"]["status"], "completed")
        self.assertEqual(
            confirmation["clarification"]["mode"],
            "test_phase_confirmation",
        )

    def test_completed_build_run_recovers_pending_tasks_overwritten_by_debug(self) -> None:
        """调试快照把任务重置为 pending 时应从同一 Build Run 的证据恢复。"""

        task_ids = ["backend-service", "frontend-page"]
        confirmation = test_phase_confirmation(
            {
                "build_summary": {},
                "build_run_id": "build-run-1",
                "build_run_plan_sha256": "sha256-plan-1",
                "build_execution_slice": {
                    "task_ids": task_ids,
                    "summary": {
                        "total": 2,
                        "completed": 2,
                        "pending": 0,
                        "running": 0,
                        "failed": 0,
                    },
                },
                "tasks": [
                    {"id": "backend-service", "status": "pending"},
                    {"id": "frontend-page", "status": "pending"},
                ],
                "build_results": [
                    {"task_id": "backend-service", "status": "failed"},
                    {"task_id": "backend-service", "status": "already_satisfied"},
                    {"task_id": "frontend-page", "status": "completed"},
                ],
                "unit_test_gate_passed": True,
                "application_name": "年龄录入",
            }
        )

        self.assertEqual(confirmation["status"], "requires_user_input")
        self.assertEqual(confirmation["build_summary"]["status"], "completed")
        self.assertEqual(confirmation["build_summary"]["completed"], 2)
        self.assertEqual(
            confirmation["clarification"]["mode"],
            "test_phase_confirmation",
        )

    def test_unit_test_debug_drops_client_build_runtime_state(self) -> None:
        """单测节点调试不得把公开快照中的 pending Build 状态写回 checkpoint。"""

        inputs = workflow_run_inputs(
            {
                "workflowDebug": {"enabled": True, "resumeFrom": "unit_test"},
                "resumeState": {
                    "state": {
                        "tasks": [{"id": "frontend-page", "status": "pending"}],
                        "buildResults": [
                            {"task_id": "frontend-page", "status": "completed"}
                        ],
                        "buildSummary": {},
                    }
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "unit_test")
        self.assertNotIn("tasks", inputs["resume_values"])
        self.assertNotIn("build_results", inputs["resume_values"])
        self.assertNotIn("build_summary", inputs["resume_values"])


if __name__ == "__main__":
    unittest.main()
