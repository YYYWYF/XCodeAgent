from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.graph.nodes.tasks import (
    _existing_build_task_plan,
)
from app.services.build_task_planner import replace_build_task_plan_tasks


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
    with_deliverable: bool = False,
) -> dict:
    """构造具备稳定 Unit、状态和依赖的最小可调度任务。"""

    task = {
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
    if with_deliverable:
        # 新生成任务必须显式声明交付物；历史基线任务刻意保留缺失字段以覆盖增量兼容边界。
        task["deliverables"] = [
            {
                "id": f"capability:{task_id}",
                "kind": "frontend.shared_capability",
                "target_id": unit_id,
                "paths": [f"frontend/src/{task_id}.ts"],
                "provides": [f"{task_id}.capability"],
            }
        ]
    return task




class BuildTaskPlanRecoveryTests(unittest.TestCase):



    def test_invalid_checkpoint_does_not_override_confirmed_formal_dag(self) -> None:
        """无效 checkpoint 不得覆盖工作区正式且已确认的 DAG。"""

        valid_plan = replace_build_task_plan_tasks(
            _base_unit_plan("frontend:api-client"),
            [_task("persisted-api-task", "frontend:api-client", status="completed")],
        )
        valid_plan["confirmation_status"] = "confirmed"
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





if __name__ == "__main__":
    unittest.main()
