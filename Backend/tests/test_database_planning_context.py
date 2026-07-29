from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.graph.nodes.tasks import (
    _task_preparation_project_plan,
    _with_database_planning_context,
)
from app.services.database_planning_context import prepare_database_planning_context


def _project_plan(method: str = "POST") -> dict:
    """构造包含 MySQL 数据源和单接口契约的测试 ProjectPlan。"""

    return {
        "version": "plan-v1",
        "confirmation_status": "confirmed",
        "data_sources": [
            {
                "id": "orders",
                "name": "订单库",
                "type": "mysql",
                "tables": ["orders"],
                "schema_refs": ["orders-api#OrderCreate"],
            }
        ],
        "api_contracts": [
            {
                "id": "orders-api",
                "data_source_id": "orders",
                "schemas": {
                    "OrderCreate": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                    }
                },
                "endpoints": [
                    {
                        "id": "orders.create",
                        "method": method,
                        "path": "/orders",
                        "summary": "创建订单。",
                        "request_schema_ref": "OrderCreate",
                        "response_schema_ref": "OrderCreate",
                    }
                ],
            }
        ],
    }


def _build_context(endpoint_id: str = "orders.create") -> dict:
    """构造 endpoint scope 的最小 BuildContext。"""

    return {
        "target": {
            "type": "endpoint",
            "id": endpoint_id,
            "api_contract_id": "orders-api",
        },
        "endpoint_ids": [endpoint_id],
        "api_contract_ids": ["orders-api"],
        "data_source_ids": ["orders"],
        "required_unit_ids": [
            "backend:bootstrap",
            "database:orders",
            "backend:endpoint:orders-api:orders.create",
        ],
        "direct_endpoint_details": [
            {
                "api_contract_id": "orders-api",
                "endpoint_id": endpoint_id,
                "data_source_id": "orders",
                "method": "POST",
                "path": "/orders",
                "processing_logic": ["写入 orders 表。"],
            }
        ],
    }


def _tool_payload() -> str:
    """构造 MySQL 工具返回的真实表结构摘要。"""

    return json.dumps(
        {
            "tool": "mysql_table_info",
            "status": "ok",
            "database": "sales",
            "tables": [{"table_name": "orders", "comment": "订单表"}],
            "schemas": {
                "orders": [
                    {
                        "column_name": "id",
                        "column_type": "bigint",
                        "is_nullable": "NO",
                        "column_key": "PRI",
                        "column_default": None,
                        "extra": "auto_increment",
                        "comment": "主键",
                    },
                    {
                        "column_name": "status",
                        "column_type": "varchar(32)",
                        "is_nullable": "NO",
                        "column_key": "",
                        "column_default": None,
                        "extra": "",
                        "comment": "状态",
                    },
                ]
            },
        },
        ensure_ascii=False,
    )


class DatabasePlanningContextTests(unittest.TestCase):
    def test_mutation_endpoint_reads_mysql_summary_for_task_planning(self) -> None:
        """写接口任务规划前会读取真实数据库摘要并生成 DatabasePlanningContext。"""

        tool = SimpleNamespace(invoke=Mock(return_value=_tool_payload()))
        with patch(
            "app.services.database_schema_summary.get_mysql_table_info",
            tool,
        ):
            context = prepare_database_planning_context(_project_plan(), _build_context())

        tool.invoke.assert_called_once_with({"table_name": "orders"})
        self.assertEqual(context["status"], "completed")
        self.assertEqual(context["contexts"][0]["data_source_id"], "orders")
        self.assertEqual(context["contexts"][0]["database"], "sales")
        self.assertEqual(context["contexts"][0]["tables"][0]["table_name"], "orders")
        self.assertEqual(
            context["contexts"][0]["api_contract"]["endpoints"][0]["id"],
            "orders.create",
        )
        self.assertEqual(
            context["contexts"][0]["endpoint_detail"]["processing_logic"],
            ["写入 orders 表。"],
        )
        self.assertTrue(context["contexts"][0]["schema_hash"])
        self.assertIn("AG-UI", context["todo"])

    def test_readonly_endpoint_skips_database_planning_context(self) -> None:
        """只读接口不会在任务规划前扫描数据库。"""

        tool = SimpleNamespace(invoke=Mock(return_value=_tool_payload()))
        with patch(
            "app.services.database_schema_summary.get_mysql_table_info",
            tool,
        ):
            plan = _project_plan(method="GET")
            plan["api_contracts"][0]["endpoints"][0] = {
                "id": "orders.list",
                "method": "GET",
                "path": "/orders",
                "summary": "查询订单列表。",
                "response_schema_ref": "OrderCreate",
            }
            context = prepare_database_planning_context(
                plan,
                {**_build_context("orders.list"), "direct_endpoint_details": []},
            )

        tool.invoke.assert_not_called()
        self.assertEqual(context["status"], "skipped")
        self.assertEqual(context["reason"], "no_database_mutation_endpoint")

    def test_task_preparation_view_includes_database_planning_context(self) -> None:
        """任务规划模型输入同时包含数据库摘要、EndpointDetail 和 API Contract。"""

        tool = SimpleNamespace(invoke=Mock(return_value=_tool_payload()))
        with patch(
            "app.services.database_schema_summary.get_mysql_table_info",
            tool,
        ):
            build_context = _with_database_planning_context(
                _project_plan(),
                _build_context(),
            )
        preparation_view = _task_preparation_project_plan(_project_plan(), build_context)
        executable_details = preparation_view["executable_details"]

        self.assertEqual(
            executable_details["database_planning_context"]["contexts"][0]["data_source_id"],
            "orders",
        )
        self.assertEqual(
            executable_details["endpoint_detail_plans"][0]["endpoint_id"],
            "orders.create",
        )
        self.assertEqual(
            executable_details["api_contracts"][0]["endpoints"][0]["id"],
            "orders.create",
        )


if __name__ == "__main__":
    unittest.main()
