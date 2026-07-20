from __future__ import annotations

import unittest

from app.services.api_contract_validation import validate_api_contract_consistency


class ApiContractValidationTests(unittest.TestCase):
    def test_page_detail_endpoint_reference_does_not_require_data_source_dependency(self) -> None:
        """页面只声明 endpoint 依赖时，校验器应通过 ProjectPlan 契约解析数据源。"""

        project_plan = {
            "data_sources": [{"id": "orders", "schema_refs": []}],
            "api_contracts": [
                {
                    "id": "orders-api",
                    "data_source_id": "orders",
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
            "data_sources": [],
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


if __name__ == "__main__":
    unittest.main()
