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
from app.services.build_context_resolver import resolve_target_build_context
from app.services.database_planning_context import (
    database_context_requirement,
    prepare_database_planning_context,
)


def _project_plan(method: str = "POST") -> dict:
    """构造包含 MySQL 数据源和单接口契约的测试 ProjectPlan。"""

    return {
        "version": "plan-v1",
        "confirmation_status": "confirmed",
        "data_sources": [{"id": "orders", "name": "订单库", "type": "mysql"}],
        "api_contracts": [
            {
                "id": "orders-api",
                "data_source_id": "orders",
                "schemas": {
                    "OrderCreate": {
                        "type": "object",
                        "required": ["status"],
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


def _tool_payload(*, database_exists: bool = True, tables: dict | None = None) -> str:
    """构造 MySQL 工具返回的真实结构摘要。"""

    schemas = tables if tables is not None else {
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
    }
    return json.dumps(
        {
            "tool": "mysql_table_info",
            "status": "ok",
            "database": "sales",
            "database_exists": database_exists,
            "tables": [
                {"table_name": name, "comment": "订单表"}
                for name in schemas
            ],
            "schemas": schemas if database_exists else {},
            "indexes": {},
            "foreign_keys": {},
        },
        ensure_ascii=False,
    )


def _summary_project_plan() -> dict:
    """构造概览聚合接口的 ProjectPlan，响应字段不等同于数据库字段。"""

    return {
        "version": "plan-v1",
        "confirmation_status": "confirmed",
        "data_sources": [{"id": "core_management_source", "type": "database"}],
        "api_contracts": [
            {
                "id": "core_management_api",
                "data_source_id": "core_management_source",
                "schemas": {
                    "CoreManagementSummary": {
                        "type": "object",
                        "required": ["totalStaff", "newEntries", "departures"],
                        "properties": {
                            "totalStaff": {"type": "integer"},
                            "newEntries": {"type": "integer"},
                            "departures": {"type": "integer"},
                            "recentChanges": {"type": "array"},
                        },
                    }
                },
                "endpoints": [
                    {
                        "id": "core_management.summary",
                        "method": "GET",
                        "path": "/api/core-management/summary",
                        "response_schema_ref": "CoreManagementSummary",
                    }
                ],
            }
        ],
    }


def _summary_build_context() -> dict:
    """构造使用已有 user 表聚合生成概览的 BuildContext。"""

    return {
        "target": {
            "type": "endpoint",
            "id": "core_management.summary",
            "api_contract_id": "core_management_api",
        },
        "endpoint_ids": ["core_management.summary"],
        "api_contract_ids": ["core_management_api"],
        "data_source_ids": ["core_management_source"],
        "direct_endpoint_details": [
            {
                "api_contract_id": "core_management_api",
                "endpoint_id": "core_management.summary",
                "data_source_id": "core_management_source",
                "method": "GET",
                "path": "/api/core-management/summary",
                "data_origin": {
                    "source_type": "mysql_existing",
                    "effective_source": {
                        "kind": "mysql_existing",
                        "database": "xcode",
                        "tables": ["user"],
                    },
                    "field_mappings": [
                        {
                            "target_field": "totalStaff",
                            "source": "user 表行计数",
                            "rule": "SELECT COUNT(*) FROM user",
                        },
                        {
                            "target_field": "newEntries",
                            "source": "无对应列，暂时无法计算",
                            "rule": "返回 0",
                        },
                        {
                            "target_field": "recentChanges[].id",
                            "source": "user.id",
                            "rule": "直接映射，但类型为int → string",
                        },
                        {
                            "target_field": "recentChanges[].department",
                            "source": "无对应列",
                            "rule": "返回 null",
                        },
                    ],
                    "differences": [
                        {
                            "field": "recentChanges[].department",
                            "actual": "user 表无 department 列",
                            "expected": "允许为空的部门展示字段",
                            "resolution_kind": "backend_adaptation",
                            "operation_refs": [],
                            "backend_adaptation": {
                                "strategy": "default_value",
                                "value": None,
                                "temporary": True,
                                "description": "当前接口明确返回 null，不修改数据库。",
                            },
                        }
                    ],
                    "database_operations": [],
                },
            }
        ],
    }


def _summary_tool_payload() -> str:
    """构造已有 user 表能支撑概览查询的数据库摘要。"""

    return _tool_payload(
        tables={
            "user": [
                {
                    "column_name": "id",
                    "column_type": "int",
                    "is_nullable": "NO",
                    "column_key": "PRI",
                    "column_default": None,
                    "extra": "",
                    "comment": "",
                },
                {
                    "column_name": "name",
                    "column_type": "varchar(64)",
                    "is_nullable": "YES",
                    "column_key": "",
                    "column_default": None,
                    "extra": "",
                    "comment": "",
                },
                {
                    "column_name": "status",
                    "column_type": "varchar(16)",
                    "is_nullable": "YES",
                    "column_key": "",
                    "column_default": None,
                    "extra": "",
                    "comment": "",
                },
            ]
        }
    )


class DatabaseContextV1Tests(unittest.TestCase):
    def test_existing_table_without_gaps_completes_new_context(self) -> None:
        """数据库满足需求时生成新版 completed 上下文且不含任务意图。"""

        tool = SimpleNamespace(invoke=Mock(return_value=_tool_payload()))
        with patch("app.services.database_schema_summary.get_mysql_table_info", tool):
            context = prepare_database_planning_context(_project_plan(), _build_context())

        tool.invoke.assert_called_once_with({"table_name": None})
        self.assertEqual(context["schema_version"], "database-context.v1")
        self.assertEqual(context["status"], "completed")
        self.assertEqual(context["actual_schema"]["database"], "sales")
        self.assertEqual(context["actual_schema"]["tables"][0]["name"], "orders")
        self.assertEqual(context["gaps"], [])
        self.assertEqual(context["task_intents"], [])

    def test_missing_table_becomes_database_task_intent(self) -> None:
        """目标表不存在时不报错，而是转成 missing_table gap 和建表意图。"""

        build_context = _build_context()
        detail = build_context["direct_endpoint_details"][0]
        detail["data_origin"] = {
            "source_type": "mysql_new_table",
            "effective_source": {
                "kind": "mysql_new_table",
                "database": "sales",
                "tables": ["orders"],
            },
            "field_mappings": [],
            "differences": [
                {
                    "field": "orders",
                    "expected": "订单持久化表",
                    "actual": "数据库不存在 orders 表",
                    "resolution_kind": "database_change",
                    "operation_refs": ["create-orders"],
                    "backend_adaptation": None,
                }
            ],
            "database_operations": [
                {
                    "id": "create-orders",
                    "operation": "create_table",
                    "database": "sales",
                    "table": {
                        "name": "orders",
                        "comment": "订单表",
                        "columns": [
                            {
                                "name": "status",
                                "type": "varchar(32)",
                                "nullable": False,
                                "default": None,
                                "comment": "状态",
                            }
                        ],
                        "primary_key": [],
                        "indexes": [],
                        "foreign_keys": [],
                    },
                }
            ],
        }
        tool = SimpleNamespace(invoke=Mock(return_value=_tool_payload(tables={})))
        with patch("app.services.database_schema_summary.get_mysql_table_info", tool):
            context = prepare_database_planning_context(_project_plan(), build_context)

        self.assertEqual(context["status"], "completed")
        self.assertEqual(context["gaps"][0]["kind"], "missing_table")
        self.assertEqual(context["gaps"][0]["table"], "orders")
        self.assertEqual(context["task_intents"][0]["operation"], "create_table")

    def test_missing_database_becomes_database_gap(self) -> None:
        """目标数据库不存在时仍完成上下文检查，并把建库转成后续任务。"""

        tool = SimpleNamespace(invoke=Mock(return_value=_tool_payload(database_exists=False)))
        with patch("app.services.database_schema_summary.get_mysql_table_info", tool):
            context = prepare_database_planning_context(_project_plan(), _build_context())

        self.assertEqual(context["status"], "completed")
        self.assertFalse(context["actual_schema"]["database_exists"])
        self.assertEqual(context["gaps"][0]["kind"], "missing_database")
        self.assertEqual(context["task_intents"][0]["operation"], "create_database")

    def test_summary_backend_adaptation_does_not_create_column_gaps(self) -> None:
        """聚合响应和返回 null/0 的字段不应被误判为必须补库。"""

        tool = SimpleNamespace(invoke=Mock(return_value=_summary_tool_payload()))
        with patch("app.services.database_schema_summary.get_mysql_table_info", tool):
            context = prepare_database_planning_context(
                _summary_project_plan(),
                _summary_build_context(),
            )

        required_table = context["required_schema"]["tables"][0]
        self.assertEqual(required_table["name"], "user")
        self.assertEqual(
            {column["name"] for column in required_table["columns"]},
            {"id"},
        )
        self.assertEqual(context["gaps"], [])
        self.assertEqual(context["task_intents"], [])
        self.assertIn(
            "backend_adaptation",
            {item["resolution_kind"] for item in context["resolution_items"]},
        )

    def test_existing_table_add_column_operation_becomes_task_intent(self) -> None:
        """mysql_existing 的结构化 add_column 应生成缺字段 gap 和任务意图。"""

        build_context = _build_context()
        detail = build_context["direct_endpoint_details"][0]
        detail["data_origin"]["differences"] = [
            {
                "field": "department",
                "expected": "持久化部门字段",
                "actual": "orders 表缺少 department",
                "resolution_kind": "database_change",
                "operation_refs": ["add-orders-department"],
                "backend_adaptation": None,
            }
        ]
        detail["data_origin"]["database_operations"] = [
            {
                "id": "add-orders-department",
                "operation": "add_column",
                "database": "sales",
                "table": "orders",
                "column": "department",
                "from": None,
                "to": {
                    "type": "varchar(128)",
                    "nullable": False,
                    "default": None,
                    "comment": "所属部门",
                },
                "reason": "订单需要持久化部门字段",
                "source_fields": ["department"],
            }
        ]

        tool = SimpleNamespace(invoke=Mock(return_value=_tool_payload()))
        with patch("app.services.database_schema_summary.get_mysql_table_info", tool):
            context = prepare_database_planning_context(_project_plan(), build_context)

        self.assertEqual(context["gaps"][0]["kind"], "missing_column")
        self.assertEqual(context["gaps"][0]["column"], "department")
        self.assertEqual(context["task_intents"][0]["operation"], "add_column")

    def test_existing_table_backend_default_does_not_create_gap(self) -> None:
        """结构化 backend_adaptation 默认值不得被转换成数据库字段。"""

        build_context = _build_context()
        detail = build_context["direct_endpoint_details"][0]
        detail["data_origin"]["differences"] = [
            {
                "field": "remark",
                "expected": "备注字段",
                "actual": "orders 表缺少 remark",
                "resolution_kind": "backend_adaptation",
                "operation_refs": [],
                "backend_adaptation": {
                    "strategy": "default_value",
                    "value": "",
                    "temporary": False,
                    "description": "后端返回空字符串。",
                },
            }
        ]
        detail["data_origin"]["database_operations"] = []

        tool = SimpleNamespace(invoke=Mock(return_value=_tool_payload()))
        with patch("app.services.database_schema_summary.get_mysql_table_info", tool):
            context = prepare_database_planning_context(_project_plan(), build_context)

        self.assertEqual(context["gaps"], [])
        self.assertEqual(context["task_intents"], [])

    def test_existing_table_default_change_becomes_task_intent(self) -> None:
        """结构化默认值变更应生成 default_mismatch 和对应任务意图。"""

        build_context = _build_context()
        detail = build_context["direct_endpoint_details"][0]
        detail["data_origin"]["database_operations"] = [
            {
                "id": "default-orders-status",
                "operation": "alter_column_default",
                "database": "sales",
                "table": "orders",
                "column": "status",
                "from": {"default": None},
                "to": {"default": {"kind": "literal", "value": "active"}},
                "reason": "新订单默认启用",
                "source_fields": ["status"],
            }
        ]

        tool = SimpleNamespace(invoke=Mock(return_value=_tool_payload()))
        with patch("app.services.database_schema_summary.get_mysql_table_info", tool):
            context = prepare_database_planning_context(_project_plan(), build_context)

        self.assertEqual(context["gaps"][0]["kind"], "default_mismatch")
        self.assertEqual(
            context["task_intents"][0]["operation"],
            "alter_column_default",
        )

    def test_connection_failure_blocks_database_context_only(self) -> None:
        """连接失败是唯一阻断状态，会返回 connection_failed。"""

        tool = SimpleNamespace(
            invoke=Mock(
                return_value=json.dumps(
                    {
                        "tool": "get_mysql_table_info",
                        "status": "error",
                        "error": "Connection failed: auth denied",
                    },
                    ensure_ascii=False,
                )
            )
        )
        with patch("app.services.database_schema_summary.get_mysql_table_info", tool):
            context = prepare_database_planning_context(_project_plan(), _build_context())

        self.assertEqual(context["status"], "connection_failed")
        self.assertEqual(context["connection"]["status"], "failed")
        self.assertEqual(context["gaps"], [])

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

    def test_task_preparation_view_includes_new_database_context(self) -> None:
        """任务规划模型输入包含新版数据库上下文和已确认接口详情。"""

        tool = SimpleNamespace(invoke=Mock(return_value=_tool_payload()))
        with patch("app.services.database_schema_summary.get_mysql_table_info", tool):
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
            executable_details["database_planning_context"]["schema_version"],
            "database-context.v1",
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
