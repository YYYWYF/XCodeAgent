from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.graph.nodes.tasks import (
    _task_preparation_project_plan,
    _with_database_planning_context_from_state,
)
from app.services.database_planning_context import (
    database_context_requirement,
    prepare_database_planning_context,
)
from app.services.build_context_resolver import resolve_target_build_context


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
                "data_origin": {
                    "source_type": "mysql_existing",
                    "effective_source": {
                        "kind": "mysql_existing",
                        "data_source_id": "orders",
                        "database": "sales",
                        "tables": ["orders"],
                    },
                    "field_mappings": [],
                    "differences": [],
                    "notes": [],
                },
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

    def test_readonly_database_endpoint_reads_mysql_summary(self) -> None:
        """只读接口只要 data_origin 来自数据库，也会在任务规划前扫描数据库。"""

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
            build_context = _build_context("orders.list")
            build_context["direct_endpoint_details"][0]["method"] = "GET"
            build_context["direct_endpoint_details"][0]["processing_logic"] = [
                "查询 orders 表。"
            ]
            context = prepare_database_planning_context(
                plan,
                build_context,
            )

        tool.invoke.assert_called_once_with({"table_name": "orders"})
        self.assertEqual(context["status"], "completed")
        self.assertEqual(context["contexts"][0]["data_source_id"], "orders")

    def test_third_party_endpoint_skips_database_context_node(self) -> None:
        """外部 API 来源不会触发数据库上下文检查节点。"""

        build_context = _build_context()
        build_context["direct_endpoint_details"][0]["data_origin"] = {
            "source_type": "third_party",
            "effective_source": {"kind": "third_party", "name": "remote-api"},
            "field_mappings": [],
            "differences": [],
            "notes": [],
        }
        requirement = database_context_requirement(_project_plan(), build_context)

        self.assertFalse(requirement["required"])
        self.assertEqual(requirement["status"], "not_required")

    def test_third_party_endpoint_scope_excludes_database_unit(self) -> None:
        """外部 API endpoint 的构建范围不应包含 database Unit。"""

        with tempfile.TemporaryDirectory() as tmpdir:
            detail_path = Path(tmpdir) / "endpoint.json"
            detail_path.write_text(
                json.dumps(
                    {
                        **_build_context()["direct_endpoint_details"][0],
                        "status": "confirmed",
                        "data_origin": {
                            "source_type": "third_party",
                            "effective_source": {
                                "kind": "third_party",
                                "name": "remote-api",
                            },
                            "field_mappings": [],
                            "differences": [],
                            "notes": [],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            plan = _project_plan()
            plan["api_contracts"][0]["endpoints"][0]["detail_design"] = {
                "status": "confirmed",
                "json_path": str(detail_path),
            }

            context = resolve_target_build_context(
                plan,
                target_type="endpoint",
                target_id="orders.create",
                api_contract_id="orders-api",
            )

        self.assertNotIn("database:orders", context["required_unit_ids"])
        self.assertIn(
            "backend:endpoint:orders-api:orders.create",
            context["required_unit_ids"],
        )

    def test_task_preparation_view_includes_database_planning_context(self) -> None:
        """任务规划模型输入同时包含数据库摘要、EndpointDetail 和 API Contract。"""

        tool = SimpleNamespace(invoke=Mock(return_value=_tool_payload()))
        with patch(
            "app.services.database_schema_summary.get_mysql_table_info",
            tool,
        ):
            database_context = prepare_database_planning_context(
                _project_plan(),
                _build_context(),
            )
            build_context = _with_database_planning_context_from_state(
                {"database_planning_context": database_context},
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
