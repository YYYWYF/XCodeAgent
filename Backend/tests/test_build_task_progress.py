from __future__ import annotations

import unittest

from app.services.build_task_progress import (
    BuildTaskProgressTracker,
    DAG_GENERATION_STAGES,
    build_task_artifacts,
    project_artifact_output,
    project_build_context_output,
    project_candidate_tasks_output,
    project_compiled_tasks_output,
    project_contract_validation_output,
    project_dag_validation_output,
    project_unit_skeleton_output,
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
                    "owner": "backend",
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

    def test_stage_outputs_are_frozen_and_cover_every_generation_phase(self) -> None:
        """每个阶段拥有独立结构化产物，后续计划变化不得覆盖已完成阶段。"""

        tracker = BuildTaskProgressTracker()
        plan = {
            "unit_skeleton": {"reused": True},
            "build_units": {
                "frontend:shell": {
                    "kind": "frontend",
                    "status": "prepared",
                    "task_ids": ["shell"],
                }
            },
            "unit_graph": {
                "schema_version": "build-unit-graph.v3",
                "edges": [{"from": "application:root", "to": "frontend:shell", "type": "contains"}],
                "validation": {"is_valid": True, "errors": []},
            },
            "task_registry": {
                "shell": {
                    "id": "shell",
                    "title": "生成壳页面",
                    "owner": "frontend",
                    "status": "pending",
                    "dependencies": [],
                    "change_scope": [{"path": "Frontend/src/App.tsx"}],
                    "acceptance_criteria": ["可启动"],
                }
            },
            "task_graph": {
                "roots": ["shell"],
                "leaves": ["shell"],
                "topological_order": ["shell"],
                "edges": [],
                "validation": {"is_valid": True, "errors": []},
            },
            "execution": {"batches": [{"mode": "serial", "tasks": ["shell"]}]},
            "summary": {"frontend": 1, "backend": 0, "database": 0},
        }

        unit_output = project_unit_skeleton_output(plan)
        tracker.complete(
            "unit_skeleton",
            "骨架完成",
            build_task_plan=plan,
            output=unit_output,
        )
        unit_output["units"][0]["status"] = "tampered"
        plan["build_units"]["frontend:shell"]["status"] = "completed"
        tracker.complete(
            "build_context",
            "上下文完成",
            build_task_plan=plan,
            output=project_build_context_output(
                {
                    "target": {"type": "page", "id": "home"},
                    "required_unit_ids": ["frontend:shell"],
                    "endpoint_ids": [],
                    "api_contract_ids": [],
                    "data_source_ids": [],
                },
                plan,
            ),
        )
        tracker.complete(
            "contract_validation",
            "契约完成",
            output=project_contract_validation_output({}, []),
        )
        tracker.complete("model_planning", "候选任务完成", output=project_candidate_tasks_output(plan))
        tracker.complete("task_compilation", "编译完成", output=project_compiled_tasks_output(plan))
        tracker.complete("dag_validation", "校验完成", output=project_dag_validation_output(plan))
        artifacts = build_task_artifacts("/workspace/BUILD_TASK_DAG.md")
        tracker.complete(
            "artifact_persistence",
            "产物完成",
            artifacts=artifacts,
            output=project_artifact_output(artifacts),
        )

        snapshot = tracker.snapshot()
        outputs = {stage["id"]: stage.get("output") for stage in snapshot["stages"]}
        self.assertEqual(
            {output["kind"] for output in outputs.values() if output},
            {
                "unit_graph",
                "build_context",
                "contract_validation",
                "candidate_tasks",
                "compiled_tasks",
                "dag_validation",
                "artifacts",
            },
        )
        self.assertEqual(outputs["unit_skeleton"]["units"][0]["status"], "prepared")
        self.assertEqual(outputs["task_compilation"]["tasks"][0]["id"], "shell")
        self.assertEqual(outputs["artifact_persistence"]["count"], 2)
        self.assertEqual(snapshot["tasks"][0]["id"], "shell")
        self.assertEqual(snapshot["artifacts"][0]["id"], "build_task_plan")

    def test_failed_stage_keeps_partial_output_and_failure_detail(self) -> None:
        """失败阶段仍保留已投射产物，供前端展开查看失败证据。"""

        tracker = BuildTaskProgressTracker()
        tracker.fail(
            "contract_validation",
            "契约校验发现 1 个问题：缺少 endpoint",
            output=project_contract_validation_output(
                {
                    "endpoint_ids": ["orders.list"],
                    "api_contract_ids": ["orders-api"],
                },
                ["缺少 endpoint: orders.list"],
            ),
        )

        stage = next(
            stage for stage in tracker.snapshot()["stages"] if stage["id"] == "contract_validation"
        )
        self.assertEqual(stage["status"], "failed")
        self.assertIn("缺少 endpoint", stage["detail"])
        self.assertEqual(stage["output"]["issues"], ["缺少 endpoint: orders.list"])

    def test_projection_caps_stage_records_and_text(self) -> None:
        """阶段列表复用 200 条记录和 1000 字符字段上限。"""

        registry = {
            f"task-{index}": {
                "id": f"task-{index}",
                "title": f"任务 {index}",
                "owner": "frontend",
                "status": "pending",
            }
            for index in range(201)
        }
        plan = {
            "task_registry": registry,
            "task_graph": {
                "nodes": list(registry),
                "validation": {"is_valid": True, "errors": []},
            },
            "build_units": {
                f"unit-{index}": {"kind": "frontend", "status": "prepared"}
                for index in range(201)
            },
        }

        self.assertEqual(len(project_candidate_tasks_output(plan)["tasks"]), 200)
        self.assertEqual(len(project_unit_skeleton_output(plan)["units"]), 200)

        tracker = BuildTaskProgressTracker()
        tracker.fail("unit_skeleton", "x" * 2_000)
        detail = tracker.snapshot()["stages"][0]["detail"]
        self.assertEqual(len(detail), 1_000)

    def test_edge_projection_is_bounded(self) -> None:
        """依赖边超过上限时只发送前 500 条并标记截断。"""

        plan = {
            "unit_graph": {
                "edges": [
                    {"from": f"unit:{index}", "to": f"unit:{index + 1}", "type": "depends_on"}
                    for index in range(501)
                ],
                "validation": {"is_valid": True, "errors": []},
            },
            "build_units": {},
        }
        output = project_unit_skeleton_output(plan)
        self.assertEqual(len(output["edges"]["items"]), 500)
        self.assertTrue(output["edges"]["truncated"])


if __name__ == "__main__":
    unittest.main()
