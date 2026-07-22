from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from app.graph.subgraphs.build import build, run_build_scheduler
from app.services.build_task_planner import replace_build_task_plan_tasks
from app.services.build_scheduler import verify_task_file_changes


def _write_workspace_file(workspace: str | None, rel_path: str) -> None:
    """模拟 agent 向工作区写入文件，使 capture_workspace_changes 能检测到变更。"""
    if not workspace:
        return
    full_path = os.path.join(workspace, rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(f"// auto-generated: {rel_path}\n")


class BuildSubgraphSchedulerTests(unittest.TestCase):
    def test_file_changes_are_attributed_to_each_task_scope(self) -> None:
        """同 owner 批次的实际变更只能记到授权路径命中的任务。"""

        results = verify_task_file_changes(
            results=[
                {"task_id": "page-a", "status": "completed"},
                {"task_id": "page-b", "status": "completed"},
            ],
            code_change_set={"files": [{"path": "Frontend/src/PageA.tsx"}]},
            tasks=[
                {"id": "page-a", "allowed_paths": ["Frontend/src/PageA.tsx"]},
                {"id": "page-b", "allowed_paths": ["Frontend/src/PageB.tsx"]},
            ],
        )

        self.assertEqual(results[0]["changed_files"], ["Frontend/src/PageA.tsx"])
        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(results[1]["status"], "failed")
        self.assertEqual(results[1]["failure_category"], "no_file_changes")

    def test_build_scheduler_runs_dependency_order_until_complete(self) -> None:
        runner_skill_sets: list[list[str] | None] = []
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
            runner_skill_sets.append(kwargs.get("selected_skill_names"))
            _write_workspace_file(kwargs.get("workspace"), "Backend/app/api.py")
            return [
                {
                    "task_id": task["id"],
                    "owner": task["owner"],
                    "status": "completed",
                }
                for task in kwargs["tasks"]
            ]

        def frontend_runner(**kwargs):
            runner_skill_sets.append(kwargs.get("selected_skill_names"))
            _write_workspace_file(kwargs.get("workspace"), "Frontend/src/Page.tsx")
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
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v2",
                                "build_units": {
                                    "application:root": {
                                        "id": "application:root",
                                        "kind": "application",
                                        "task_ids": ["api", "page"],
                                    }
                                },
                                "unit_graph": {"nodes": ["application:root"], "edges": []},
                            },
                            tasks,
                        ),
                        "timeline": [],
                        "selected_skill_names": ["workflow-skill"],
                    }
                )

        self.assertEqual(result["build_summary"]["status"], "completed")
        self.assertEqual(result["build_summary"]["completed"], 2)
        self.assertEqual([task["status"] for task in result["tasks"]], ["completed", "completed"])
        self.assertIn("scheduler:dispatch:api", result["build_events"])
        self.assertIn("scheduler:dispatch:page", result["build_events"])
        self.assertEqual(runner_skill_sets, [["workflow-skill"], ["workflow-skill"]])

    def test_build_scheduler_streams_task_progress_snapshots(self) -> None:
        """调度器应在任务运行和结果应用时输出当前执行切片。"""

        progress_events: list[dict] = []
        tasks = [
            {
                "id": "api",
                "unit_id": "data-source:orders",
                "owner": "data_source",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"path": "Backend/app/orders.py"}],
            },
            {
                "id": "page",
                "unit_id": "page:orders",
                "owner": "frontend",
                "status": "pending",
                "dependencies": ["api"],
                "change_scope": [{"path": "Frontend/src/Orders.tsx"}],
            },
        ]

        def complete_runner(**kwargs):
            for task in kwargs["tasks"]:
                change = task.get("change_scope", [{}])[0]
                _write_workspace_file(kwargs.get("workspace"), str(change.get("path") or ""))
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
                    side_effect=complete_runner,
                ),
                patch(
                    "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                    side_effect=complete_runner,
                ),
            ):
                result = run_build_scheduler(
                    {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_execution_scope": {
                            "type": "page",
                            "targetId": "orders",
                        },
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v2",
                                "build_units": {
                                    "data-source:orders": {
                                        "id": "data-source:orders",
                                        "kind": "data_source",
                                    },
                                    "page:orders": {"id": "page:orders", "kind": "page"},
                                },
                                "unit_graph": {
                                    "nodes": ["data-source:orders", "page:orders"],
                                    "edges": [
                                        {
                                            "from": "data-source:orders",
                                            "to": "page:orders",
                                            "type": "depends_on",
                                        }
                                    ],
                                },
                            },
                            tasks,
                        ),
                        "timeline": [],
                    },
                    progress_writer=progress_events.append,
                )

        self.assertEqual(result["build_summary"]["status"], "completed")
        self.assertGreaterEqual(len(progress_events), 4)
        first_slice = progress_events[0]["state"]["build_execution_slice"]
        self.assertEqual(first_slice["scope"], {"type": "page", "targetId": "orders"})
        self.assertEqual(first_slice["summary"]["running"], 1)
        self.assertEqual(
            {task["id"]: task["status"] for task in first_slice["tasks"]},
            {"api": "running", "page": "pending"},
        )
        final_slice = progress_events[-1]["state"]["build_execution_slice"]
        self.assertEqual(final_slice["summary"]["completed"], 2)
        self.assertTrue(
            all(task["status"] == "completed" for task in final_slice["tasks"])
        )

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
                    _write_workspace_file(kwargs.get("workspace"), "Frontend/src/Page.tsx")
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
                ) as repair_planner,
            ):
                result = build(
                    {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v2",
                                "build_units": {
                                    "application:root": {
                                        "id": "application:root",
                                        "kind": "application",
                                        "task_ids": ["page"],
                                    }
                                },
                                "unit_graph": {"nodes": ["application:root"], "edges": []},
                            },
                            tasks,
                        ),
                        "timeline": [],
                        "selected_skill_names": ["repair-skill"],
                    }
                )

        self.assertEqual(result["build_summary"]["status"], "completed")
        self.assertEqual(result["tasks"][0]["status"], "completed")
        self.assertTrue(result["tasks"][0]["completed_by_repair"])
        self.assertEqual(result["repair_task_plan"]["status"], "ready")
        self.assertIn("scheduler:repair_planned:1", result["build_events"])
        self.assertEqual(
            repair_planner.call_args.kwargs["selected_skill_names"],
            ["repair-skill"],
        )

    def test_page_scope_does_not_execute_unrelated_page_tasks(self) -> None:
        """页面范围只调度目标页面闭包内任务，不更新其他页面任务。"""

        dispatched_task_ids: list[str] = []
        tasks = [
            {
                "id": "orders-api",
                "unit_id": "data-source:orders",
                "owner": "data_source",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"path": "Backend/app/orders.py"}],
            },
            {
                "id": "orders-page",
                "unit_id": "page:orders",
                "owner": "frontend",
                "status": "pending",
                "dependencies": ["orders-api"],
                "change_scope": [{"path": "Frontend/src/Orders.tsx"}],
            },
            {
                "id": "customers-page",
                "unit_id": "page:customers",
                "owner": "frontend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"path": "Frontend/src/Customers.tsx"}],
            },
        ]

        def complete_runner(**kwargs):
            dispatched_task_ids.extend(task["id"] for task in kwargs["tasks"])
            for task in kwargs["tasks"]:
                change = task.get("change_scope", [{}])[0]
                _write_workspace_file(kwargs.get("workspace"), str(change.get("path") or ""))
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
                    side_effect=complete_runner,
                ),
                patch(
                    "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                    side_effect=complete_runner,
                ),
            ):
                result = build(
                    {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_execution_scope": {"type": "page", "targetId": "orders"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v2",
                                "build_units": {
                                    "data-source:orders": {
                                        "id": "data-source:orders",
                                        "kind": "data_source",
                                    },
                                    "page:orders": {"id": "page:orders", "kind": "page"},
                                    "page:customers": {
                                        "id": "page:customers",
                                        "kind": "page",
                                    },
                                },
                                "unit_graph": {
                                    "nodes": [
                                        "data-source:orders",
                                        "page:orders",
                                        "page:customers",
                                    ],
                                    "edges": [
                                        {
                                            "from": "data-source:orders",
                                            "to": "page:orders",
                                            "type": "depends_on",
                                        }
                                    ],
                                },
                            },
                            tasks,
                        ),
                        "timeline": [],
                    }
                )

        self.assertEqual(dispatched_task_ids, ["orders-api", "orders-page"])
        statuses = {task["id"]: task["status"] for task in result["tasks"]}
        self.assertEqual(statuses["orders-api"], "completed")
        self.assertEqual(statuses["orders-page"], "completed")
        self.assertEqual(statuses["customers-page"], "pending")
        self.assertEqual(result["build_execution_slice"]["task_ids"], ["orders-api", "orders-page"])
        self.assertEqual(result["build_summary"]["status"], "completed")

    def test_incoming_integration_repair_task_enters_page_scope_and_counts_on_dispatch(self) -> None:
        """测试修复任务必须追加到最新计划、命中页面切片并在真实派发后计数。"""

        dispatched_task_ids: list[str] = []
        tasks = [
            {
                "id": "orders-page",
                "unit_id": "page:orders",
                "owner": "frontend",
                "status": "completed",
                "dependencies": [],
                "allowed_paths": ["Frontend/src/Orders.tsx"],
                "change_scope": [{"path": "Frontend/src/Orders.tsx"}],
            }
        ]
        repair_task = {
            "id": "repair:plan-1:frontend_build:frontend",
            "task_id": "repair:plan-1:frontend_build:frontend",
            "kind": "repair",
            "unit_id": "page:orders",
            "owner": "frontend",
            "status": "pending",
            "dependencies": [],
            "allowed_paths": ["Frontend/src/Orders.tsx"],
            "change_scope": [{"path": "Frontend/src/Orders.tsx"}],
        }

        def repair_runner(**kwargs):
            dispatched_task_ids.extend(task["id"] for task in kwargs["tasks"])
            _write_workspace_file(kwargs.get("workspace"), "Frontend/src/Orders.tsx")
            return [
                {"task_id": task["id"], "owner": "frontend", "status": "completed"}
                for task in kwargs["tasks"]
            ]

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.subgraphs.build.generate_frontend_with_deep_agent",
                side_effect=repair_runner,
            ):
                result = run_build_scheduler(
                    {
                        "workspace": workspace,
                        "project_plan": {"version": "1.0.0"},
                        "build_execution_scope": {"type": "page", "targetId": "orders"},
                        "build_task_plan": replace_build_task_plan_tasks(
                            {
                                "schema_version": "build-dag.v2",
                                "build_units": {"page:orders": {"id": "page:orders", "kind": "page"}},
                                "unit_graph": {"nodes": ["page:orders"], "edges": []},
                            },
                            tasks,
                        ),
                        "tasks": tasks,
                        "repair_task_plan": {
                            "status": "ready",
                            "decision": "repair",
                            "tasks": [repair_task],
                        },
                        "repair_iteration": 0,
                    }
                )

        self.assertEqual(dispatched_task_ids, [repair_task["id"]])
        self.assertIn(repair_task["id"], result["build_execution_slice"]["task_ids"])
        self.assertEqual(result["repair_iteration"], 1)
        self.assertEqual(result["build_summary"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
