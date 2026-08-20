from __future__ import annotations

import unittest

from app.services.detail_review import apply_detail_review_submission
from tests.entity_design_test_utils import confirm_entity_designs
from app.services.page_detail_plan import (
    create_page_detail_plan,
    extract_page_detail_context,
)
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec


class DetailReviewTests(unittest.TestCase):
    def test_endpoint_decision_edit_recomposes_endpoint_detail(self) -> None:
        """用户调整接口行为决策后，应从同一 EndpointDecision 重新生成派生字段。"""

        plan = {
            "frontend_pages": [],
            "page_detail_plans": [],
            "entities": [
                {
                    "id": "User",
                    "name": "User",
                    "fields": [],
                }
            ],
            "api_contracts": [
                {
                    "id": "user_api",
                    "entity_ids": ["User"],
                    "schemas": {
                        "UserResponse": {"type": "object", "properties": {}}
                    },
                    "endpoints": [
                        {
                            "id": "user_api.delete",
                            "method": "DELETE",
                            "path": "/users",
                            "response_schema_ref": "UserResponse",
                        }
                    ],
                }
            ],
            "endpoint_detail_plans": [
                {
                    "api_contract_id": "user_api",
                    "endpoint_id": "user_api.delete",
                    "method": "DELETE",
                    "path": "/users/{id}",
                    "endpoint_decision": {
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
        plan = confirm_entity_designs(plan, source_type="external_api")

        result = apply_detail_review_submission(
            plan,
            {
                "review_status": "confirmed",
                "target_changes": [
                    {
                        "target_type": "endpoint",
                        "target_id": "user_api:user_api.delete",
                        "changes": {
                            "endpoint_decision": {
                                "operation_semantics": {
                                    "operation_kind": "delete",
                                    "target_cardinality": "exactly_one",
                                    "selector": {
                                        "source": "path",
                                        "fields": ["id"],
                                    },
                                    "transaction_required": True,
                                    "zero_match_behavior": "返回 404",
                                    "multiple_match_behavior": "拒绝执行",
                                    "success_status_code": 204,
                                    "side_effect": "delete",
                                },
                                "risks": ["需要用户确认删除范围。"],
                            }
                        },
                    }
                ],
            },
            selected_api_contract_id="user_api",
            selected_endpoint_id="user_api.delete",
        )

        detail = result["endpoint_detail_plans"][0]
        self.assertEqual(detail["design_stage"], "complete")
        self.assertNotIn("data_origin", detail)
        self.assertNotIn("data_source_id", detail)
        self.assertNotIn("data_origin", detail["endpoint_decision"])
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

    def test_endpoint_data_origin_is_contract_controlled(self) -> None:
        """接口不再设计数据来源：data_origin 编辑必须被契约控制拒绝。"""

        plan = {
            "frontend_pages": [],
            "page_detail_plans": [],
            "entities": [
                {
                    "id": "User",
                    "name": "User",
                    "fields": [],
                }
            ],
            "api_contracts": [
                {
                    "id": "user_api",
                    "entity_ids": ["User"],
                    "endpoints": [
                        {
                            "id": "user-list",
                            "method": "GET",
                            "path": "/users",
                        }
                    ],
                }
            ],
            "endpoint_detail_plans": [
                {
                    "api_contract_id": "user_api",
                    "endpoint_id": "user-list",
                    "method": "GET",
                    "path": "/users",
                    "endpoint_decision": {
                        "operation_semantics": {
                            "operation_kind": "read",
                            "target_cardinality": "many",
                            "selector": {"source": "none", "fields": []},
                            "transaction_required": False,
                            "zero_match_behavior": "返回空列表",
                            "multiple_match_behavior": "返回全部",
                            "success_status_code": 200,
                            "side_effect": "none",
                        },
                        "risks": [],
                    },
                }
            ],
        }
        plan = confirm_entity_designs(plan, source_type="database")

        with self.assertRaisesRegex(ValueError, "contract-controlled"):
            apply_detail_review_submission(
                plan,
                {
                    "review_status": "confirmed",
                    "target_changes": [
                        {
                            "target_type": "endpoint",
                            "target_id": "user_api:user-list",
                            "changes": {
                                "data_origin": {"source_type": "database"}
                            },
                        }
                    ],
                },
                selected_endpoint_id="user-list",
            )


if __name__ == "__main__":
    unittest.main()
