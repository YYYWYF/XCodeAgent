from __future__ import annotations

import unittest
from dataclasses import asdict

from app.domain.models import BuildTask


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


if __name__ == "__main__":
    unittest.main()
