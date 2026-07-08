from __future__ import annotations

import unittest

from app.graph.workflow import route_requirements, route_workflow_start


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


if __name__ == "__main__":
    unittest.main()
