from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from app.graph.subgraphs.build import build


class BuildSubgraphSchedulerTests(unittest.TestCase):
    def test_build_scheduler_runs_dependency_order_until_complete(self) -> None:
        tasks = [
            {
                "id": "api",
                "owner": "data_source",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"path": "Backend/app/api.py"}],
            },
            {
                "id": "page",
                "owner": "frontend",
                "status": "pending",
                "dependencies": ["api"],
                "change_scope": [{"path": "Frontend/src/Page.tsx"}],
            },
        ]

        def data_runner(**kwargs):
            return [
                {
                    "task_id": task["id"],
                    "owner": task["owner"],
                    "status": "completed",
                }
                for task in kwargs["tasks"]
            ]

        def frontend_runner(**kwargs):
            return [
                {
                    "task_id": task["id"],
                    "owner": task["owner"],
                    "status": "completed",
                }
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.graph.subgraphs.build.generate_data_sources_with_deep_agent",
                    side_effect=data_runner,
                ),
                patch(
                    "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                    side_effect=frontend_runner,
                ),
            ):
                result = build(
                    {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": {
                            "version": "0.3.0",
                            "tasks": tasks,
                            "summary": {"total": 2},
                        },
                        "tasks": tasks,
                        "timeline": [],
                    }
                )

        self.assertEqual(result["build_summary"]["status"], "completed")
        self.assertEqual(result["build_summary"]["completed"], 2)
        self.assertEqual([task["status"] for task in result["tasks"]], ["completed", "completed"])
        self.assertIn("scheduler:dispatch:api", result["build_events"])
        self.assertIn("scheduler:dispatch:page", result["build_events"])

    def test_build_scheduler_plans_and_runs_repair_task(self) -> None:
        tasks = [
            {
                "id": "page",
                "owner": "frontend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"path": "Frontend/src/Page.tsx"}],
                "allowed_paths": ["Frontend/src/**"],
                "acceptance_criteria": ["页面可渲染"],
            },
        ]

        def frontend_runner(**kwargs):
            results = []
            for task in kwargs["tasks"]:
                if task.get("kind") == "repair":
                    results.append(
                        {
                            "task_id": task["id"],
                            "owner": task["owner"],
                            "status": "completed",
                        }
                    )
                else:
                    results.append(
                        {
                            "task_id": task["id"],
                            "owner": task["owner"],
                            "status": "failed",
                            "failure_category": "test_failure",
                            "failure_signature": "test_failure:page",
                        }
                    )
            return results

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                    side_effect=frontend_runner,
                ),
                patch(
                    "app.graph.subgraphs.build.plan_build_failure_repair_with_repair_planner_agent",
                    return_value={
                        "decision": "repair",
                        "strategy": "修复页面测试失败。",
                        "boundaries": {},
                        "repair_tasks": [
                            {
                                "title": "修复页面测试失败",
                                "description": "只在原任务范围内修复测试失败。",
                            }
                        ],
                    },
                ),
            ):
                result = build(
                    {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": {
                            "version": "0.3.0",
                            "tasks": tasks,
                            "summary": {"total": 1},
                        },
                        "tasks": tasks,
                        "timeline": [],
                    }
                )

        self.assertEqual(result["build_summary"]["status"], "completed")
        self.assertEqual(result["tasks"][0]["status"], "completed")
        self.assertTrue(result["tasks"][0]["completed_by_repair"])
        self.assertEqual(result["repair_task_plan"]["status"], "ready")
        self.assertIn("scheduler:repair_planned:1", result["build_events"])


if __name__ == "__main__":
    unittest.main()
