from __future__ import annotations

import unittest

from app.services.build_task_progress import (
    BuildTaskProgressTracker,
    DAG_GENERATION_STAGES,
    build_task_artifacts,
)


class BuildTaskProgressTests(unittest.TestCase):
    def test_tracker_emits_complete_ordered_snapshots(self) -> None:
        """每次更新都发送固定七阶段完整快照，并保留稳定顺序。"""

        events: list[dict] = []
        tracker = BuildTaskProgressTracker(events.append)

        tracker.start("unit_skeleton", "正在生成骨架")
        tracker.complete("unit_skeleton", "骨架完成")
        tracker.start("build_context", "正在解析上下文")

        self.assertEqual(len(events), 3)
        self.assertEqual(
            [stage["id"] for stage in events[-1]["dag_generation"]["stages"]],
            [stage_id for stage_id, _ in DAG_GENERATION_STAGES],
        )
        self.assertEqual(events[-1]["dag_generation"]["stages"][0]["status"], "completed")
        self.assertEqual(events[-1]["dag_generation"]["stages"][1]["status"], "running")

    def test_snapshot_projects_topological_tasks_and_safe_fields(self) -> None:
        """有效 DAG 按拓扑序投射任务，且不携带模型原文和内部 JSON。"""

        tracker = BuildTaskProgressTracker()
        plan = {
            "agent_note": "secret raw model response",
            "build_units": {"page:home": {"id": "page:home"}},
            "task_registry": {
                "page": {
                    "id": "page",
                    "title": "实现首页",
                    "owner": "frontend",
                    "status": "pending",
                    "dependencies": ["api"],
                    "change_scope": [{"path": "frontend/src/pages/Home.tsx"}],
                    "acceptance_criteria": ["首页可渲染"],
                },
                "api": {
                    "id": "api",
                    "title": "实现首页 API",
                    "owner": "data_source",
                    "status": "pending",
                    "dependencies": [],
                    "change_scope": [{"path": "backend/app/api.py"}],
                    "acceptance_criteria": ["接口返回成功"],
                },
            },
            "task_graph": {
                "nodes": ["page", "api"],
                "topological_order": ["api", "page"],
                "edges": [{"from": "api", "to": "page"}],
                "validation": {"is_valid": True, "errors": []},
            },
            "summary": {"frontend": 1, "data_source": 1},
            "execution": {"batches": [{"tasks": ["api"]}, {"tasks": ["page"]}]},
        }

        tracker.complete("task_compilation", "任务已编译", build_task_plan=plan)
        snapshot = tracker.snapshot()

        self.assertEqual([task["id"] for task in snapshot["tasks"]], ["api", "page"])
        self.assertEqual(snapshot["tasks"][1]["changePaths"], ["frontend/src/pages/Home.tsx"])
        self.assertEqual(snapshot["summary"]["batchCount"], 2)
        self.assertNotIn("agent_note", str(snapshot))
        self.assertNotIn(".json", str(snapshot))

    def test_invalid_dag_keeps_every_registry_task(self) -> None:
        """无效 DAG 不得按部分拓扑序裁掉尚未排序的注册任务。"""

        tracker = BuildTaskProgressTracker()
        plan = {
            "task_registry": {
                "first": {"id": "first", "title": "第一项", "status": "pending"},
                "second": {"id": "second", "title": "第二项", "status": "pending"},
            },
            "task_graph": {
                "nodes": ["first", "second"],
                "topological_order": ["first"],
                "validation": {"is_valid": False, "errors": ["cycle"]},
            },
        }

        tracker.fail("dag_validation", "存在循环", build_task_plan=plan)

        self.assertEqual(
            [task["id"] for task in tracker.snapshot()["tasks"]],
            ["first", "second"],
        )

    def test_artifacts_hide_internal_json_path(self) -> None:
        """产物投影只公开 Markdown 路径，内部计划仅显示安全标签。"""

        artifacts = build_task_artifacts("/workspace/.xcodeagent/plans/BUILD_TASK_DAG.md")

        self.assertNotIn("path", artifacts[0])
        self.assertEqual(artifacts[0]["kind"], "internal")
        self.assertTrue(artifacts[1]["path"].endswith("BUILD_TASK_DAG.md"))


if __name__ == "__main__":
    unittest.main()
