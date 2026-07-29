from __future__ import annotations

import unittest
from dataclasses import asdict

from app.domain.models import BuildTask, DatabasePlanningContext


class BuildDagV3ContractTests(unittest.TestCase):
    def test_build_task_accepts_database_backend_frontend_contract_fields(self) -> None:
        """验证 v3 任务合同包含 owner、能力、数据库范围、风险和审批字段。"""

        task = BuildTask(
            id="db-orders-migration",
            owner="database",
            description="Prepare orders tables.",
            unit_id="database:orders",
            task_type="database.change",
            provides_capabilities=["database:orders:ready"],
            database_scope={"data_source_id": "orders", "operations": ["create_table"]},
            risk="high",
            approval={"required": True, "reason": "schema migration"},
        )

        payload = asdict(task)

        self.assertEqual(payload["owner"], "database")
        self.assertEqual(payload["task_type"], "database.change")
        self.assertEqual(payload["unit_id"], "database:orders")
        self.assertEqual(payload["risk"], "high")
        self.assertTrue(payload["approval"]["required"])
        self.assertEqual(payload["provides_capabilities"], ["database:orders:ready"])

    def test_database_planning_context_keeps_real_database_summary(self) -> None:
        """验证真实数据库摘要可作为任务规划上下文独立保存。"""

        context = DatabasePlanningContext(
            data_source_id="orders",
            summary="orders database has order_header and order_item tables.",
            tables=[{"name": "order_header", "columns": ["id", "status"]}],
            captured_at="2026-07-29T00:00:00Z",
            schema_hash="hash-v1",
        )

        payload = asdict(context)

        self.assertEqual(payload["source"], "database-tool")
        self.assertEqual(payload["data_source_id"], "orders")
        self.assertEqual(payload["tables"][0]["name"], "order_header")


if __name__ == "__main__":
    unittest.main()
