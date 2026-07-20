from __future__ import annotations

import unittest

from app.services.build_scheduler import resolve_execution_slice


class ExecutionSliceResolverTests(unittest.TestCase):
    def test_page_scope_includes_direct_prerequisites_only(self) -> None:
        """页面范围只包含目标页面、公共前置和直接数据源任务。"""

        build_task_plan = {
            "unit_graph": {
                "nodes": [
                    "app:api-client",
                    "data-source:orders",
                    "data-source:customers",
                    "page:orders",
                    "page:customers",
                ],
                "edges": [
                    {"from": "app:api-client", "to": "page:orders", "type": "depends_on"},
                    {"from": "data-source:orders", "to": "page:orders", "type": "depends_on"},
                    {"from": "data-source:customers", "to": "page:customers", "type": "depends_on"},
                ],
            }
        }
        tasks = [
            {"id": "api-client", "unit_id": "app:api-client", "status": "completed"},
            {"id": "orders-api", "unit_id": "data-source:orders", "status": "pending"},
            {"id": "customers-api", "unit_id": "data-source:customers", "status": "pending"},
            {"id": "orders-page", "unit_id": "page:orders", "status": "pending"},
            {"id": "customers-page", "unit_id": "page:customers", "status": "pending"},
        ]

        execution_slice = resolve_execution_slice(
            build_task_plan=build_task_plan,
            tasks=tasks,
            build_execution_scope={"type": "page", "targetId": "orders"},
        )

        self.assertEqual(
            execution_slice["unit_ids"],
            ["page:orders", "app:api-client", "data-source:orders"],
        )
        self.assertEqual(
            execution_slice["task_ids"],
            ["api-client", "orders-api", "orders-page"],
        )
        self.assertEqual(execution_slice["reusable_task_ids"], ["api-client"])

    def test_data_source_scope_includes_backend_prerequisite(self) -> None:
        """数据源范围会包含后端公共前置 Unit，不加载页面任务。"""

        build_task_plan = {
            "unit_graph": {
                "nodes": ["app:backend-bootstrap", "data-source:orders", "page:orders"],
                "edges": [
                    {
                        "from": "app:backend-bootstrap",
                        "to": "data-source:orders",
                        "type": "depends_on",
                    },
                    {"from": "data-source:orders", "to": "page:orders", "type": "depends_on"},
                ],
            }
        }
        tasks = [
            {"id": "backend", "unit_id": "app:backend-bootstrap", "status": "completed"},
            {"id": "orders-api", "unit_id": "data-source:orders", "status": "pending"},
            {"id": "orders-page", "unit_id": "page:orders", "status": "pending"},
        ]

        execution_slice = resolve_execution_slice(
            build_task_plan=build_task_plan,
            tasks=tasks,
            build_execution_scope={"type": "data_source", "targetId": "orders"},
        )

        self.assertEqual(
            execution_slice["unit_ids"],
            ["data-source:orders", "app:backend-bootstrap"],
        )
        self.assertEqual(execution_slice["task_ids"], ["backend", "orders-api"])


if __name__ == "__main__":
    unittest.main()
