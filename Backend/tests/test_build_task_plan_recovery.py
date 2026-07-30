from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.graph.nodes.tasks import (
    _existing_build_task_plan,
    _merge_prepared_scope_tasks,
    _resolve_build_context,
)
from app.services.build_task_planner import (
    create_build_task_plan,
    replace_build_task_plan_tasks,
)


def _base_unit_plan(*unit_ids: str, edges: list[dict] | None = None) -> dict:
    """构造带有效空任务图的最小 Unit 计划，供增量合并回归测试复用。"""

    return {
        "schema_version": "build-dag.v3",
        "build_units": {
            unit_id: {
                "id": unit_id,
                "kind": "application" if unit_id.startswith("app:") else "page",
                "status": "not_prepared",
                "task_ids": [],
                "depends_on_unit_ids": [],
                "source_refs": {},
            }
            for unit_id in unit_ids
        },
        "unit_graph": {
            "schema_version": "build-unit-graph.v3",
            "nodes": list(unit_ids),
            "edges": list(edges or []),
            "validation": {"is_valid": True, "errors": []},
        },
        "task_registry": {},
        "task_graph": {
            "schema_version": "build-task-graph.v3",
            "nodes": [],
            "edges": [],
            "topological_order": [],
            "validation": {"is_valid": True, "errors": []},
        },
    }


def _task(
    task_id: str,
    unit_id: str,
    *,
    status: str = "pending",
    dependencies: list[str] | None = None,
) -> dict:
    """构造具备稳定 Unit、状态和依赖的最小可调度任务。"""

    return {
        "id": task_id,
        "unit_id": unit_id,
        "owner": "frontend",
        "status": status,
        "dependencies": list(dependencies or []),
        "change_scope": [
            {
                "operation": "modify",
                "path": f"frontend/src/{task_id}.ts",
                "description": "执行测试任务。",
            }
        ],
        "target_files": [f"frontend/src/{task_id}.ts"],
    }


class BuildTaskPlanRecoveryTests(unittest.TestCase):
    def test_invalid_checkpoint_falls_back_to_last_valid_persisted_dag(self) -> None:
        """无效 checkpoint 不得覆盖工作区中最后一次通过校验的 DAG。"""

        valid_plan = replace_build_task_plan_tasks(
            _base_unit_plan("frontend:api-client"),
            [_task("persisted-api-task", "frontend:api-client", status="completed")],
        )
        invalid_plan = deepcopy(valid_plan)
        invalid_plan["task_graph"]["validation"] = {
            "is_valid": False,
            "errors": ["Task page depends on missing task stale-api-task."],
        }
        invalid_plan["task_registry"] = {
            "poisoned-task": _task("poisoned-task", "frontend:api-client")
        }

        with tempfile.TemporaryDirectory() as workspace:
            plan_path = Path(workspace) / ".xcodeagent/plans/build-task-plan.json"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(json.dumps(valid_plan), encoding="utf-8")
            resolved = _existing_build_task_plan(
                {"workspace": workspace, "build_task_plan": invalid_plan}
            )

        self.assertEqual(set(resolved["task_registry"]), {"persisted-api-task"})
        self.assertTrue(resolved["task_graph"]["validation"]["is_valid"])

    def test_application_scope_retains_only_completed_reusable_app_units(self) -> None:
        """应用范围必须保留已完成公共 Unit，并替换未完成业务 Unit。"""

        base_plan = replace_build_task_plan_tasks(
            _base_unit_plan(
                "frontend:shell",
                "frontend:auth-guard",
                "page:dashboard",
                edges=[
                    {
                        "from": "frontend:shell",
                        "to": "page:dashboard",
                        "type": "depends_on",
                    }
                ],
            ),
            [
                _task("shared-shell-task", "frontend:shell", status="completed"),
                _task("pending-auth-task", "frontend:auth-guard"),
                _task(
                    "old-dashboard-task",
                    "page:dashboard",
                    dependencies=["shared-shell-task"],
                ),
            ],
        )
        build_context = _resolve_build_context(
            {},
            {"version": "1.0.0"},
            {"type": "application", "targetId": "application"},
            base_plan,
        )
        prepared_plan = create_build_task_plan(
            {"version": "1.0.0"},
            agent_plan={
                "tasks": [
                    {
                        **_task("new-dashboard-task", "page:dashboard"),
                        "description": "重新生成概览页。",
                    }
                ]
            },
            base_build_task_plan=base_plan,
            build_context=build_context,
        )

        merged = _merge_prepared_scope_tasks(base_plan, prepared_plan, build_context)

        self.assertEqual(
            build_context["reusable_tasks_by_unit"],
            {"frontend:shell": ["shared-shell-task"]},
        )
        self.assertEqual(
            set(merged["task_registry"]),
            {"shared-shell-task", "new-dashboard-task"},
        )
        self.assertTrue(merged["task_graph"]["validation"]["is_valid"])

    def test_page_scope_rewrites_retained_dependency_after_target_replacement(self) -> None:
        """页面任务 ID 改变后，保留的集成任务必须改为依赖新页面任务。"""

        base_plan = replace_build_task_plan_tasks(
            _base_unit_plan(
                "page:dashboard",
                "app:integration",
                edges=[
                    {
                        "from": "page:dashboard",
                        "to": "app:integration",
                        "type": "depends_on",
                    }
                ],
            ),
            [
                _task("old-dashboard-task", "page:dashboard"),
                _task(
                    "integration-task",
                    "app:integration",
                    dependencies=["old-dashboard-task"],
                ),
            ],
        )
        build_context = {
            "target": {"type": "page", "id": "dashboard"},
            "required_unit_ids": ["page:dashboard"],
            "source_refs": {},
        }
        prepared_plan = create_build_task_plan(
            {"version": "1.0.0"},
            agent_plan={
                "tasks": [
                    {
                        **_task("new-dashboard-task", "page:dashboard"),
                        "description": "重新生成概览页。",
                    }
                ]
            },
            base_build_task_plan=base_plan,
            build_context=build_context,
        )

        merged = _merge_prepared_scope_tasks(base_plan, prepared_plan, build_context)

        self.assertNotIn("old-dashboard-task", merged["task_registry"])
        self.assertEqual(
            merged["task_registry"]["integration-task"]["dependencies"],
            ["new-dashboard-task"],
        )
        self.assertTrue(merged["task_graph"]["validation"]["is_valid"])


if __name__ == "__main__":
    unittest.main()
