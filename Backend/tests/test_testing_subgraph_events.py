from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.subgraphs.testing import build_testing_subgraph
from app.workspace.code_changes import CapturedWorkspaceChanges


class TestingSubgraphEventsTests(unittest.TestCase):
    """Regression guard: test_events must accumulate across testing-subgraph
    nodes instead of being overwritten by the last node.

    Before the fix, ``ProjectState.test_events`` had no ``add`` reducer, so each
    node's ``test_events`` return value replaced the previous one and the final
    timeline only contained the last node's marker. The frontend test timeline
    (projected from ``test_events``) was therefore incomplete.
    """

    def test_test_events_accumulate_across_all_nodes(self) -> None:
        subgraph = build_testing_subgraph()

        test_agent_value = {"reviewed_by": "test_agent", "agent_note": "ok"}

        with patch(
            "app.graph.subgraphs.testing.run_integration_checks",
            return_value={
                "test_results": [{"id": "frontend_install", "passed": True}],
                "test_events": ["frontend_install", "backend_install"],
            },
        ), patch(
            "app.graph.subgraphs.testing.validate_api_contract_consistency",
            return_value=[],
        ), patch(
            "app.graph.subgraphs.testing.summarize_tests_with_deep_agent",
            return_value=test_agent_value,
        ) as test_agent, patch(
            "app.graph.subgraphs.testing.capture_agent_file_changes",
            side_effect=lambda **kwargs: CapturedWorkspaceChanges(
                value=kwargs["action"](), code_change_set=None
            ),
        ), patch(
            "app.graph.subgraphs.testing.evaluate_quality_gate",
            return_value={
                "passed": True,
                "needs_revision": False,
                "revision_requests": [],
            },
        ), patch(
            "app.graph.subgraphs.testing.write_test_report_json",
            return_value="/tmp/test_report.json",
        ), patch(
            "app.graph.subgraphs.testing.plan_repairs_with_repair_planner_agent",
            return_value={"decision": "terminal_failure", "status": "terminal_failure", "tasks": []},
        ), patch(
            "app.graph.subgraphs.testing.write_repair_task_plan_json",
            return_value="/tmp/repair_task_plan.json",
        ):
            result = subgraph.invoke(
                {
                    "workspace": "/tmp/workspace",
                    "build_summary": {"failed": 0, "pending": 0},
                    "test_results": [],
                    "test_events": [],
                    "code_changes": {},
                    "code_change_sets": [],
                    "timeline": [],
                    "selected_skill_names": ["workflow-skill"],
                }
            )

        events = result.get("test_events", [])
        # Every node contributes a marker; with the add reducer they accumulate
        # instead of being overwritten by the last node.
        self.assertIn("frontend_install", events)
        self.assertIn("backend_install", events)
        self.assertIn("api_contract", events)
        self.assertIn("test_agent_review", events)
        self.assertIn("main_quality_gate", events)
        self.assertIn("repair_planning:skipped", events)
        # Order follows node execution order.
        self.assertEqual(
            events,
            [
                "frontend_install",
                "backend_install",
                "api_contract",
                "test_agent_review",
                "main_quality_gate",
                "repair_planning:skipped",
            ],
        )
        self.assertEqual(
            test_agent.call_args.kwargs["selected_skill_names"],
            ["workflow-skill"],
        )


if __name__ == "__main__":
    unittest.main()
