from __future__ import annotations

import unittest

from app.services.detail_review import apply_detail_review_submission
from app.services.page_detail_plan import (
    create_page_detail_plan,
    extract_page_detail_context,
)
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec


class DetailReviewTests(unittest.TestCase):
    def test_user_data_origin_decision_recomposes_endpoint_detail(self) -> None:
        """用户闭合来源决策后，应从同一 EndpointDecision 重新生成派生字段。"""

        pending_origin = {
            "source_type": "needs_user_confirmation",
            "effective_source": {
                "kind": "needs_user_confirmation",
                "description": "请选择实现来源",
            },
            "field_mappings": [],
            "differences": [
                {
                    "field": "data_source",
                    "expected": "明确来源",
                    "actual": "尚未决定",
                    "resolution_kind": "needs_user_confirmation",
                    "operation_refs": [],
                    "backend_adaptation": None,
                }
            ],
            "database_operations": [],
            "notes": [],
        }
        plan = {
            "frontend_pages": [],
            "page_detail_plans": [],
            "api_contracts": [],
            "endpoint_detail_plans": [
                {
                    "api_contract_id": "user_api",
                    "endpoint_id": "user_api.delete",
                    "method": "DELETE",
                    "path": "/users/{id}",
                    "data_origin": pending_origin,
                    "endpoint_decision": {
                        "data_origin": pending_origin,
                        "operation_semantics": {
                            "operation_kind": "delete",
                            "target_cardinality": "exactly_one",
                            "selector": {"source": "path", "fields": ["id"]},
                            "transaction_required": True,
                            "zero_match_behavior": "返回 404",
                            "multiple_match_behavior": "拒绝执行",
                            "success_status_code": 204,
                            "side_effect": "delete",
                        },
                        "risks": [],
                    },
                    "processing_logic": [],
                    "acceptance_criteria": [],
                }
            ],
        }
        resolved_origin = {
            "source_type": "mock",
            "effective_source": {"kind": "mock", "description": "内存数据"},
            "field_mappings": [],
            "differences": [],
            "database_operations": [],
            "notes": [],
        }

        result = apply_detail_review_submission(
            plan,
            {
                "review_status": "confirmed",
                "target_changes": [
                    {
                        "target_type": "endpoint",
                        "target_id": "user_api:user_api.delete",
                        "changes": {"data_origin": resolved_origin},
                    }
                ],
            },
            selected_api_contract_id="user_api",
            selected_endpoint_id="user_api.delete",
        )

        detail = result["endpoint_detail_plans"][0]
        self.assertEqual(detail["design_stage"], "complete")
        self.assertEqual(
            detail["endpoint_decision"]["data_origin"]["source_type"],
            "mock",
        )
        self.assertTrue(detail["processing_logic"])
        self.assertTrue(detail["acceptance_criteria"])

    def setUp(self) -> None:
        self.plan = create_project_plan(create_requirement_spec("创建库存管理系统"))
        page_context = extract_page_detail_context(
            self.plan,
            self.plan["frontend_pages"][0]["pageId"],
        )
        self.plan["page_detail_plans"] = [
            create_page_detail_plan(self.plan, page_context)
        ]

    def test_contract_controlled_fields_cannot_be_changed(self) -> None:
        pageId = self.plan["page_detail_plans"][0]["pageId"]

        with self.assertRaisesRegex(ValueError, "contract-controlled"):
            apply_detail_review_submission(
                self.plan,
                {
                    "review_status": "confirmed",
                    "target_changes": [
                        {
                            "target_type": "page",
                            "target_id": pageId,
                            "changes": {"response_bindings": []},
                        }
                    ],
                },
            )

    def test_no_change_confirmation_marks_the_whole_plan_confirmed(self) -> None:
        result = apply_detail_review_submission(
            self.plan,
            {"review_status": "confirmed", "target_changes": []},
        )

        self.assertEqual(result["confirmation_status"], "confirmed")
        self.assertEqual(result["page_detail_plans"][0]["status"], "confirmed")

    def test_confirmation_repairs_bindings_from_external_detail_references(self) -> None:
        detail = self.plan["page_detail_plans"][0]
        page = next(
            item
            for item in self.plan["frontend_pages"]
            if item["pageId"] == detail["pageId"]
        )
        detail.pop("api_dependencies", None)
        detail["references"] = {
            "endpoint_dependencies": page["references"]["endpoint_dependencies"]
        }
        detail["response_bindings"] = []

        result = apply_detail_review_submission(
            self.plan,
            {"review_status": "confirmed", "target_changes": []},
        )

        confirmed_detail = result["page_detail_plans"][0]
        self.assertTrue(confirmed_detail["api_dependencies"])
        self.assertTrue(confirmed_detail["response_bindings"])

    def test_confirmation_rejects_unresolved_endpoint_data_origin(self) -> None:
        """确认 endpoint 详情时必须先解决待确认的数据来源。"""

        plan = {
            "frontend_pages": [],
            "page_detail_plans": [],
            "api_contracts": [],
            "endpoint_detail_plans": [
                {
                    "api_contract_id": "user_api",
                    "endpoint_id": "user-roles",
                    "data_origin": {
                        "source_type": "needs_user_confirmation",
                        "effective_source": {
                            "kind": "needs_user_confirmation",
                            "description": "需确认新建 role 表或从 user 表派生",
                        },
                    },
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "still needs user confirmation"):
            apply_detail_review_submission(
                plan,
                {"review_status": "confirmed", "target_changes": []},
                selectedPageId="user_page",
            )

    def test_confirmation_accepts_resolved_endpoint_data_origin(self) -> None:
        """用户把数据来源改为确定方案后允许确认 endpoint 详情。"""

        plan = {
            "frontend_pages": [],
            "page_detail_plans": [],
            "api_contracts": [],
            "endpoint_detail_plans": [
                {
                    "api_contract_id": "user_api",
                    "endpoint_id": "user-roles",
                    "data_origin": {
                        "source_type": "needs_user_confirmation",
                        "effective_source": {
                            "kind": "needs_user_confirmation",
                            "description": "需确认新建 role 表或从 user 表派生",
                        },
                    },
                }
            ],
        }

        result = apply_detail_review_submission(
            plan,
            {
                "review_status": "confirmed",
                "target_changes": [
                    {
                        "target_type": "endpoint",
                        "target_id": "user_api:user-roles",
                        "changes": {
                            "data_origin": {
                                "source_type": "mysql_new_table",
                                "effective_source": {
                                    "kind": "mysql_new_table",
                                    "database": "xcode",
                                    "tables": ["role"],
                                },
                                "field_mappings": [],
                                "differences": [
                                    {
                                        "field": "role",
                                        "expected": "角色需要持久化",
                                        "actual": "数据库不存在 role 表",
                                        "resolution_kind": "database_change",
                                        "operation_refs": ["create-role"],
                                        "backend_adaptation": None,
                                    }
                                ],
                                "database_operations": [
                                    {
                                        "id": "create-role",
                                        "operation": "create_table",
                                        "database": "xcode",
                                        "table": {
                                            "name": "role",
                                            "comment": "角色表",
                                            "columns": [
                                                {
                                                    "name": "id",
                                                    "type": "bigint",
                                                    "nullable": False,
                                                    "default": None,
                                                    "comment": "主键",
                                                }
                                            ],
                                            "primary_key": ["id"],
                                            "indexes": [],
                                            "foreign_keys": [],
                                        },
                                        "column": None,
                                        "from": None,
                                        "to": None,
                                        "reason": "角色需要独立持久化",
                                        "source_fields": ["id"],
                                    }
                                ],
                            }
                        },
                    }
                ],
            },
            selectedPageId="user_page",
        )

        self.assertEqual(
            result["endpoint_detail_plans"][0]["data_origin"]["source_type"],
            "mysql_new_table",
        )
        self.assertEqual(result["endpoint_detail_plans"][0]["status"], "confirmed")

    def test_confirmation_rejects_database_change_without_operation(self) -> None:
        """数据库变更字段没有引用结构化操作时不得确认 EndpointDetail。"""

        plan = {
            "frontend_pages": [],
            "page_detail_plans": [],
            "api_contracts": [],
            "endpoint_detail_plans": [
                {
                    "api_contract_id": "user_api",
                    "endpoint_id": "user-list",
                    "data_origin": {
                        "source_type": "mysql_existing",
                        "effective_source": {
                            "kind": "mysql_existing",
                            "database": "xcode",
                            "tables": ["user"],
                        },
                        "field_mappings": [],
                        "differences": [
                            {
                                "field": "department",
                                "expected": "部门字段",
                                "actual": "user 表缺少字段",
                                "resolution_kind": "database_change",
                                "operation_refs": [],
                                "backend_adaptation": None,
                            }
                        ],
                        "database_operations": [],
                    },
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "requires operation_refs"):
            apply_detail_review_submission(
                plan,
                {"review_status": "confirmed", "target_changes": []},
                selectedPageId="user_page",
            )

    def test_confirmation_accepts_add_column_with_target_definition(self) -> None:
        """add_column 使用 column 名称和 to 目标定义时允许确认。"""

        plan = {
            "frontend_pages": [],
            "page_detail_plans": [],
            "api_contracts": [],
            "endpoint_detail_plans": [
                {
                    "api_contract_id": "user_api",
                    "endpoint_id": "user-list",
                    "data_origin": {
                        "source_type": "mysql_existing",
                        "effective_source": {
                            "kind": "mysql_existing",
                            "database": "xcode",
                            "tables": ["user"],
                        },
                        "field_mappings": [],
                        "differences": [
                            {
                                "field": "department",
                                "expected": "部门字段",
                                "actual": "user 表缺少字段",
                                "resolution_kind": "database_change",
                                "operation_refs": ["add-user-department"],
                                "backend_adaptation": None,
                            }
                        ],
                        "database_operations": [
                            {
                                "id": "add-user-department",
                                "operation": "add_column",
                                "database": "xcode",
                                "table": "user",
                                "column": "department",
                                "from": None,
                                "to": {
                                    "type": "varchar(128)",
                                    "nullable": True,
                                    "default": None,
                                    "comment": "所属部门",
                                },
                                "reason": "持久化部门字段",
                                "source_fields": ["department"],
                            }
                        ],
                    },
                }
            ],
        }

        result = apply_detail_review_submission(
            plan,
            {"review_status": "confirmed", "target_changes": []},
            selectedPageId="user_page",
        )

        self.assertEqual(result["endpoint_detail_plans"][0]["status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
