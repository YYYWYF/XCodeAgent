from __future__ import annotations

import unittest

from app.services.build_task_confirmation import build_task_confirmation_read_model
from app.workspace.task_documents import build_planning_provenance


def _cumulative_plan() -> dict:
    """构造带历史任务、复用依赖和本轮新增任务的累计 PendingPlan。"""

    tasks = [
        {
            "id": "history-task",
            "title": "历史任务",
            "unit_id": "page:history",
            "dependencies": [],
            "status": "completed",
        },
        {
            "id": "shared-base",
            "title": "公共基础",
            "unit_id": "application:root",
            "dependencies": [],
            "status": "completed",
        },
        {
            "id": "shared-client",
            "title": "请求客户端",
            "unit_id": "frontend:api-client",
            "dependencies": ["shared-base"],
            "status": "completed",
        },
        {
            "id": "orders-backend",
            "title": "订单接口",
            "unit_id": "backend:orders",
            "dependencies": ["shared-client"],
            "status": "pending",
        },
        {
            "id": "orders-page",
            "title": "订单页面",
            "unit_id": "page:orders",
            "dependencies": ["orders-backend"],
            "status": "pending",
        },
    ]
    task_ids = [task["id"] for task in tasks]
    return {
        "schema_version": "build-dag.v4",
        "status": "ready",
        "confirmation_status": "pending",
        "task_registry": {task["id"]: task for task in tasks},
        "task_graph": {
            "nodes": task_ids,
            "edges": [],
            "topological_order": task_ids,
            "validation": {"is_valid": True, "errors": []},
        },
        "planning_provenance": {
            "schema_version": "planning-provenance.v2",
            "review_task_ids": [
                "shared-base",
                "shared-client",
                "orders-backend",
                "orders-page",
            ],
            "platform_task_ids": [],
            "new_task_ids": ["orders-backend", "orders-page"],
            "reused_task_ids": ["shared-base", "shared-client"],
        },
    }


class BuildTaskConfirmationTests(unittest.TestCase):
    """验证确认任务只从 Pending provenance 与 Task Graph 派生。"""

    def test_provenance_contains_assembly_review_scope_in_topological_order(self) -> None:
        """Pending provenance 保留 Assembly 的 review、platform、new、reused 四组来源。"""

        plan = _cumulative_plan()
        self.assertEqual(
            build_planning_provenance(
                plan,
                ("history-task", "shared-base", "shared-client"),
                ("shared-base", "shared-client", "orders-backend", "orders-page"),
                ("shared-base", "shared-client"),
            ),
            {
                "schema_version": "planning-provenance.v2",
                "review_task_ids": [
                    "shared-base",
                    "shared-client",
                    "orders-backend",
                    "orders-page",
                ],
                "platform_task_ids": [],
                "new_task_ids": ["orders-backend", "orders-page"],
                "reused_task_ids": ["shared-base", "shared-client"],
            },
        )

    def test_classification_does_not_need_build_context(self) -> None:
        """完整 provenance 存在时，缺少 BuildContext 仍能准确分类任务。"""

        read_model = build_task_confirmation_read_model(
            _cumulative_plan(),
            {"type": "page", "targetId": "orders"},
            build_context={},
        )

        self.assertEqual(
            [(task["id"], task["reviewRole"]) for task in read_model["reviewTasks"]],
            [
                ("shared-base", "reused"),
                ("shared-client", "reused"),
                ("orders-backend", "new"),
                ("orders-page", "new"),
            ],
        )
        self.assertEqual(read_model["retainedTaskSummary"]["total"], 1)
        self.assertNotIn("classificationBlocked", read_model)

    def test_fully_reused_scope_projects_all_review_tasks(self) -> None:
        """本轮没有新增 Task 时，Confirmation 仍按 Assembly provenance 展示完整 Scope。"""

        plan = _cumulative_plan()
        plan["planning_provenance"] = build_planning_provenance(
            plan,
            tuple(plan["task_registry"]),
            ("shared-base", "shared-client", "orders-backend", "orders-page"),
            ("shared-base", "shared-client", "orders-backend", "orders-page"),
        )

        read_model = build_task_confirmation_read_model(
            plan,
            {"type": "page", "targetId": "orders"},
            build_context={},
        )

        self.assertEqual(
            [(task["id"], task["reviewRole"]) for task in read_model["reviewTasks"]],
            [
                ("shared-base", "reused"),
                ("shared-client", "reused"),
                ("orders-backend", "reused"),
                ("orders-page", "reused"),
            ],
        )
        self.assertEqual(read_model["retainedTaskSummary"]["total"], 1)
        self.assertNotIn("classificationBlocked", read_model)

    def test_platform_task_is_covered_but_hidden_from_confirmation(self) -> None:
        """平台内部 Task 必须被 provenance 覆盖，但不能进入 review 或 retained 摘要。"""

        plan = _cumulative_plan()
        platform_task = {
            "id": "platform_metadata_projection",
            "title": "Platform Metadata Projection",
            "unit_id": "application:root",
            "dependencies": [],
            "execution_strategy": "deterministic",
            "platform_executor": "template.metadata_projection",
            "status": "pending",
        }
        plan["task_registry"][platform_task["id"]] = platform_task
        plan["task_graph"]["nodes"].append(platform_task["id"])
        plan["task_graph"]["topological_order"].append(platform_task["id"])
        plan["planning_provenance"] = build_planning_provenance(
            plan,
            ("history-task", "shared-base", "shared-client"),
            ("shared-base", "shared-client", "orders-backend", "orders-page"),
            ("shared-base", "shared-client"),
            (platform_task["id"],),
        )

        read_model = build_task_confirmation_read_model(
            plan,
            {"type": "page", "targetId": "orders"},
            build_context={},
        )

        self.assertNotIn(platform_task["id"], {task["id"] for task in read_model["reviewTasks"]})
        self.assertEqual(read_model["retainedTaskSummary"]["total"], 1)
        self.assertNotIn("classificationBlocked", read_model)

    def test_pending_without_provenance_fails_closed(self) -> None:
        """Pending 缺少当前 provenance 时必须引导重新生成。"""

        plan = _cumulative_plan()
        plan.pop("planning_provenance")
        read_model = build_task_confirmation_read_model(
            plan,
            {"type": "page", "targetId": "orders"},
            build_context={},
        )

        self.assertTrue(read_model["classificationBlocked"])
        self.assertEqual(read_model["reviewTasks"], [])
        self.assertIn("planning_provenance", read_model["classificationErrors"][0])


if __name__ == "__main__":
    unittest.main()
