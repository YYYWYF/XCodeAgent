from __future__ import annotations

import unittest

from app.graph.workflow import (
    route_acceptance,
    route_build_result,
    route_detail_confirmation,
    route_prepare_build_tasks,
    route_test_validation,
    route_workflow_start,
)


class WorkflowRoutingTests(unittest.TestCase):
    def test_workflow_start_defaults_to_detail_confirmation(self) -> None:
        self.assertEqual(
            route_workflow_start({}),
            "detail_confirmation",
        )

    def test_workflow_start_can_resume_from_detail_confirmation(self) -> None:
        self.assertEqual(
            route_workflow_start({"resume_from": "detail_confirmation"}),
            "detail_confirmation",
        )

    def test_removed_planning_resume_starts_at_detail_confirmation(self) -> None:
        self.assertEqual(
            route_workflow_start({"resume_from": "project_planning"}),
            "detail_confirmation",
        )

    def test_workflow_start_can_resume_from_prepare_build_tasks(self) -> None:
        self.assertEqual(
            route_workflow_start({"resume_from": "prepare_build_tasks"}),
            "prepare_build_tasks",
        )

    def test_detail_confirmation_waits_for_user_input(self) -> None:
        self.assertEqual(
            route_detail_confirmation({"status": "requires_user_input"}),
            "await_user_input",
        )

    def test_detail_confirmation_continues_when_page_spec_is_confirmed(self) -> None:
        self.assertEqual(
            route_detail_confirmation({"status": "completed"}),
            "inspect_workspace",
        )

    def test_prepare_build_tasks_waits_for_project_plan_confirmation(self) -> None:
        self.assertEqual(
            route_prepare_build_tasks({"status": "requires_user_input"}),
            "await_user_input",
        )

    def test_prepare_build_tasks_continues_to_build_after_confirmation(self) -> None:
        self.assertEqual(
            route_prepare_build_tasks({"status": "completed"}),
            "build",
        )

    def test_integration_test_passes_to_launch_project(self) -> None:
        self.assertEqual(
            route_test_validation({"quality_gate_passed": True}),
            "launch_project",
        )

    def test_build_only_enters_testing_after_complete_summary(self) -> None:
        """构建切片完整成功后才允许进入集成测试。"""

        self.assertEqual(
            route_build_result({"build_summary": {"status": "completed"}}),
            "integration_test",
        )

    def test_build_confirmation_stops_for_user_input(self) -> None:
        """构建需要扩大范围时必须停留等待用户确认。"""

        self.assertEqual(
            route_build_result({"build_summary": {"status": "requires_confirmation"}}),
            "await_user_input",
        )

    def test_incomplete_build_routes_to_failure_instead_of_testing(self) -> None:
        """待执行、阻塞或终止失败的构建不得伪装成可测试状态。"""

        for status in ("in_progress", "needs_repair", "failed"):
            with self.subTest(status=status):
                self.assertEqual(
                    route_build_result({"build_summary": {"status": status}}),
                    "handle_failure",
                )

    def test_integration_test_repair_plan_returns_to_build(self) -> None:
        self.assertEqual(
            route_test_validation(
                {
                    "quality_gate_passed": False,
                    "integration_next_action": "repair_build",
                }
            ),
            "build",
        )

    def test_integration_test_confirmation_waits_for_user(self) -> None:
        self.assertEqual(
            route_test_validation(
                {
                    "quality_gate_passed": False,
                    "integration_next_action": "await_user_input",
                }
            ),
            "await_user_input",
        )

    def test_integration_test_terminal_failure_routes_to_failure(self) -> None:
        self.assertEqual(
            route_test_validation(
                {
                    "quality_gate_passed": False,
                    "integration_next_action": "handle_failure",
                }
            ),
            "handle_failure",
        )

    def test_acceptance_requires_structured_accepted_state(self) -> None:
        """最终完成节点只能由结构化验收通过状态解锁。"""

        self.assertEqual(route_acceptance({"accepted": True}), "finalize_project")
        self.assertEqual(route_acceptance({"accepted": False}), "await_user_input")


if __name__ == "__main__":
    unittest.main()
