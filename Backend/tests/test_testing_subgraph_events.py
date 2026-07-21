from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.subgraphs.testing import (
    api_contract_check,
    build_testing_subgraph,
    integration_test,
)
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

    def test_api_contract_check_reports_running_and_terminal_progress(self) -> None:
        """验证确定性 API 契约检查也会写入实时检查清单。"""

        progress: list[dict] = []
        with patch(
            "app.graph.subgraphs.testing.validate_api_contract_consistency",
            return_value=[],
        ):
            result = api_contract_check(
                {
                    "build_summary": {"failed": 0, "pending": 0},
                    "project_plan": {},
                    "test_results": [],
                },
                config={
                    "configurable": {
                        "integration_test_progress_reporter": progress.append,
                    }
                },
            )

        self.assertEqual(
            [event["status"] for event in progress],
            ["running", "passed"],
        )
        self.assertEqual(progress[-1]["check"]["id"], "api_contract")
        self.assertEqual(result["test_results"][0]["id"], "api_contract")

    def test_integration_test_forwards_nested_progress_as_custom_snapshot(self) -> None:
        """验证外层节点会把内部子图回调合并后写入 custom stream。"""

        emitted: list[dict] = []

        def invoke_subgraph(input_state: dict, *, config: dict) -> dict:
            """模拟子图执行并调用运行配置中的瞬态进度回调。"""

            reporter = config["configurable"]["integration_test_progress_reporter"]
            reporter(
                {
                    "status": "running",
                    "check": {
                        "id": "frontend_build",
                        "name": "前端构建检查",
                        "required": True,
                    },
                }
            )
            reporter(
                {
                    "status": "passed",
                    "check": {
                        "id": "frontend_build",
                        "name": "前端构建检查",
                        "required": True,
                        "evidence": "命令执行通过。",
                    },
                }
            )
            return {
                "test_results": [],
                "test_events": [],
                "test_agent_review": {},
                "test_report": {},
                "quality_gate_passed": True,
                "needs_revision": False,
                "revision_requests": [],
                "repair_task_plan": {},
                "repair_tasks": [],
                "integration_next_action": "launch_project",
                "code_changes": {},
                "code_change_sets": [],
            }

        with patch(
            "app.graph.subgraphs.testing.get_stream_writer",
            return_value=emitted.append,
        ), patch(
            "app.graph.subgraphs.testing._testing_subgraph.invoke",
            side_effect=invoke_subgraph,
        ):
            integration_test({"repair_iteration": 0, "max_repair_iterations": 3})

        self.assertEqual(len(emitted), 2)
        self.assertEqual(emitted[-1]["type"], "integration_test.checks")
        self.assertEqual(emitted[-1]["checks"][0]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
