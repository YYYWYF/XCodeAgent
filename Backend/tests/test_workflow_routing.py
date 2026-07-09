from __future__ import annotations

import unittest

from app.graph.workflow import (
    route_detail_confirmation,
    route_prepare_build_tasks,
    route_project_planning,
    route_requirements,
    route_workflow_start,
)


class WorkflowRoutingTests(unittest.TestCase):
    def test_workflow_start_can_resume_from_requirements(self) -> None:
        self.assertEqual(
            route_workflow_start({"resume_from": "requirements"}),
            "requirements",
        )

    def test_workflow_start_defaults_to_complexity_classification(self) -> None:
        self.assertEqual(
            route_workflow_start({}),
            "classify_request_complexity",
        )

    def test_workflow_start_can_resume_from_detail_confirmation(self) -> None:
        self.assertEqual(
            route_workflow_start({"resume_from": "detail_confirmation"}),
            "detail_confirmation",
        )

    def test_workflow_start_can_resume_from_project_planning(self) -> None:
        self.assertEqual(
            route_workflow_start({"resume_from": "project_planning"}),
            "project_planning",
        )

    def test_workflow_start_can_resume_from_prepare_build_tasks(self) -> None:
        self.assertEqual(
            route_workflow_start({"resume_from": "prepare_build_tasks"}),
            "prepare_build_tasks",
        )

    def test_requirements_waits_for_user_input_when_clarification_is_required(self) -> None:
        self.assertEqual(
            route_requirements(
                {"clarification": {"status": "requires_user_input"}}
            ),
            "await_user_input",
        )

    def test_requirements_continues_when_clarification_is_clear(self) -> None:
        self.assertEqual(
            route_requirements({"clarification": {"status": "clear"}}),
            "project_planning",
        )

    def test_project_planning_waits_for_user_confirmation(self) -> None:
        self.assertEqual(
            route_project_planning({"status": "requires_user_input"}),
            "await_user_input",
        )

    def test_project_planning_continues_after_confirmation(self) -> None:
        self.assertEqual(
            route_project_planning({"status": "completed"}),
            "detail_confirmation",
        )

    def test_detail_confirmation_waits_for_user_input(self) -> None:
        self.assertEqual(
            route_detail_confirmation({"status": "requires_user_input"}),
            "await_user_input",
        )

    def test_detail_confirmation_continues_when_page_spec_is_confirmed(self) -> None:
        self.assertEqual(
            route_detail_confirmation({"status": "completed"}),
            "prepare_build_tasks",
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


if __name__ == "__main__":
    unittest.main()
