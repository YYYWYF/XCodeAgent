from __future__ import annotations

import unittest

from app.services.api_contract_validation import validate_api_contract_consistency


class ApiContractValidationTests(unittest.TestCase):
    def test_page_detail_endpoint_reference_does_not_require_data_source_dependency(self) -> None:
        """页面只声明 endpoint 依赖时，校验器应通过 ProjectPlan 契约解析数据源。"""

        project_plan = {
            "entities": [
                {
                    "id": "Order",
                    "name": "Order",
                    "fields": [],
                    "data_source": "database",
                }
            ],
            "api_contracts": [
                {
                    "id": "orders-api",
                    "entity_ids": ["Order"],
                    "data_source_id": "database",
                    "schemas": {"OrderList": {"type": "object"}},
                    "endpoints": [
                        {
                            "id": "orders.list",
                            "method": "GET",
                            "path": "/orders",
                            "response_schema_ref": "#/schemas/OrderList",
                        }
                    ],
                }
            ],
            "page_detail_plans": [
                {
                    "pageId": "orders",
                    "references": {
                        "endpoint_dependencies": [{"endpoint_id": "orders.list"}],
                    },
                }
            ],
        }

        self.assertEqual(validate_api_contract_consistency(project_plan), [])

    def test_page_detail_unknown_endpoint_is_still_rejected(self) -> None:
        """endpoint 引用保留为页面唯一依赖时，未知 endpoint 仍必须被阻止。"""

        project_plan = {
            "entities": [],
            "api_contracts": [],
            "page_detail_plans": [
                {
                    "pageId": "orders",
                    "references": {
                        "endpoint_dependencies": [{"endpoint_id": "orders.list"}],
                    },
                }
            ],
        }

        self.assertEqual(
            validate_api_contract_consistency(project_plan),
            ["Page orders references unknown endpoint orders.list."],
        )

    def test_data_source_fields_are_not_part_of_contract_validation(self) -> None:
        """全局契约校验忽略 data_source_id、源级 Schema 和悬空 schema_refs。"""

        project_plan = {
            "entities": [{"id": "Order", "name": "Order", "fields": []}],
            "data_sources": [
                {
                    "id": "database",
                    "schema": {"legacy": "field"},
                    "schema_refs": ["missing-api#/schemas/Missing"],
                }
            ],
            "api_contracts": [
                {
                    "id": "orders-api",
                    "entity_ids": ["Order"],
                    "data_source_id": "unknown-source",
                    "schemas": {"OrderList": {"type": "object"}},
                    "endpoints": [
                        {
                            "id": "orders.list",
                            "method": "GET",
                            "path": "/orders",
                            "response_schema_ref": "#/schemas/OrderList",
                        }
                    ],
                }
            ],
        }

        self.assertEqual(validate_api_contract_consistency(project_plan), [])

    def test_unknown_contract_entity_is_still_rejected(self) -> None:
        """移除数据源校验后仍严格检查 Contract 绑定实体是否存在。"""

        project_plan = {
            "entities": [{"id": "Customer", "name": "Customer", "fields": []}],
            "api_contracts": [
                {
                    "id": "orders-api",
                    "entity_ids": ["Order"],
                    "schemas": {"OrderList": {"type": "object"}},
                    "endpoints": [
                        {
                            "id": "orders.list",
                            "method": "GET",
                            "path": "/orders",
                            "response_schema_ref": "#/schemas/OrderList",
                        }
                    ],
                }
            ],
        }

        self.assertIn(
            "API contract orders-api references unknown entity Order.",
            validate_api_contract_consistency(project_plan),
        )


if __name__ == "__main__":
    unittest.main()
