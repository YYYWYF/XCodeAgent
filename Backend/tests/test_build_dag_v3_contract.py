from __future__ import annotations

import unittest
from dataclasses import asdict

from app.domain.models import BuildTask, BuildTaskExecutionContractError
from app.services.build_task_planner import replace_build_task_plan_tasks


class BuildDagV3ContractTests(unittest.TestCase):
    def test_legacy_task_defaults_to_agent_execution(self) -> None:
        """旧 Task 未声明执行扩展时继续采用 Agent 策略。"""

        task = BuildTask(id="legacy-page", owner="frontend", description="Build page.")

        self.assertEqual(task.execution_strategy, "agent")
        self.assertIsNone(task.platform_executor)

    def test_deterministic_task_schema_accepts_allowlisted_executor(self) -> None:
        """确定性 Task 可以显式声明当前唯一平台执行器。"""

        task = BuildTask(
            id="auth-resources",
            owner="frontend",
            description="Project authorization resources.",
            execution_strategy="deterministic",
            platform_executor="authorization.frontend_resources",
        )

        self.assertEqual(task.owner, "frontend")
        self.assertEqual(task.execution_strategy, "deterministic")
        self.assertEqual(task.platform_executor, "authorization.frontend_resources")

    def test_unknown_deterministic_executor_is_rejected(self) -> None:
        """未知平台执行器不能通过领域模型构造。"""

        with self.assertRaises(BuildTaskExecutionContractError):
            BuildTask(
                id="unknown-executor",
                owner="frontend",
                description="Do not dispatch.",
                execution_strategy="deterministic",
                platform_executor="authorization.unknown",  # type: ignore[arg-type]
            )

    def test_dag_validation_rejects_unknown_executor(self) -> None:
        """字典形式的未知 executor 也必须进入 DAG validation errors。"""

        task = {
            "id": "unknown-executor",
            "owner": "frontend",
            "description": "Do not dispatch.",
            "execution_strategy": "deterministic",
            "platform_executor": "authorization.unknown",
            "dependencies": [],
            "unit_id": "application:root",
            "target_files": ["Frontend/src/Page.tsx"],
            "allowed_paths": ["Frontend/src/Page.tsx"],
            "change_scope": [{"operation": "add", "path": "Frontend/src/Page.tsx"}],
        }

        plan = replace_build_task_plan_tasks({}, [task])

        errors = plan["task_graph"]["validation"]["errors"]
        self.assertTrue(any("is not allowlisted" in error for error in errors))

    def test_execution_fields_are_serialized(self) -> None:
        """序列化必须保留明确策略和平台执行器，legacy 默认也可见。"""

        deterministic = asdict(
            BuildTask(
                id="auth-resources",
                owner="frontend",
                description="Project authorization resources.",
                execution_strategy="deterministic",
                platform_executor="authorization.frontend_resources",
            )
        )
        legacy = asdict(
            BuildTask(id="legacy-page", owner="frontend", description="Build page.")
        )

        self.assertEqual(deterministic["execution_strategy"], "deterministic")
        self.assertEqual(
            deterministic["platform_executor"],
            "authorization.frontend_resources",
        )
        self.assertEqual(legacy["execution_strategy"], "agent")
        self.assertIsNone(legacy["platform_executor"])

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
