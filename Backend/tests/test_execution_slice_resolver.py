from __future__ import annotations

import unittest

from app.services.build_scheduler import resolve_execution_slice


class ExecutionSliceResolverTests(unittest.TestCase):
    def test_page_scope_includes_direct_prerequisites_only(self) -> None:
        """页面范围只包含目标页面、公共前置和直接数据库任务。"""

        build_task_plan = {
            "unit_graph": {
                "nodes": [
                    "frontend:api-client",
                    "database:orders",
                    "database:customers",
                    "page:orders",
                    "page:customers",
                ],
                "edges": [
                    {"from": "frontend:api-client", "to": "page:orders", "type": "depends_on"},
                    {"from": "database:orders", "to": "page:orders", "type": "depends_on"},
                    {"from": "database:customers", "to": "page:customers", "type": "depends_on"},
                ],
            }
        }
        tasks = [
            {"id": "api-client", "unit_id": "frontend:api-client", "status": "completed"},
            {"id": "orders-db", "unit_id": "database:orders", "status": "pending"},
            {"id": "customers-db", "unit_id": "database:customers", "status": "pending"},
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
            ["page:orders", "frontend:api-client", "database:orders"],
        )
        self.assertEqual(
            execution_slice["task_ids"],
            ["api-client", "orders-db", "orders-page"],
        )
        self.assertEqual(execution_slice["reusable_task_ids"], ["api-client"])

    def test_data_source_scope_uses_database_unit_only(self) -> None:
        """数据源范围映射到数据库 Unit，不加载后端或页面任务。"""

        build_task_plan = {
            "unit_graph": {
                "nodes": ["backend:bootstrap", "database:orders", "page:orders"],
                "edges": [
                    {"from": "database:orders", "to": "page:orders", "type": "depends_on"},
                ],
            }
        }
        tasks = [
            {"id": "backend", "unit_id": "backend:bootstrap", "status": "completed"},
            {"id": "orders-db", "unit_id": "database:orders", "status": "pending"},
            {"id": "orders-page", "unit_id": "page:orders", "status": "pending"},
        ]

        execution_slice = resolve_execution_slice(
            build_task_plan=build_task_plan,
            tasks=tasks,
            build_execution_scope={"type": "data_source", "targetId": "orders"},
        )

        self.assertEqual(
            execution_slice["unit_ids"],
            ["database:orders"],
        )
        self.assertEqual(execution_slice["task_ids"], ["orders-db"])


if __name__ == "__main__":
    unittest.main()
