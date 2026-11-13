from __future__ import annotations

import unittest

from app.graph.nodes.tasks import _scoped_contract_errors
from app.services.api_contract_validation import (
    validate_api_contract_consistency,
    validate_api_contract_definitions,
)
from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints
from tests.test_runtime_endpoint_contracts import business, plan


class ApiContractValidationTests(unittest.TestCase):
    def test_scoped_runtime_endpoint_validates_against_complete_plan(self) -> None:
        """单接口规划能解析正式 Agent 的页面和 Tool，派生路由被改写时仍阻止开发。"""

        project_plan = plan()
        project_plan["artifact_type"] = "technical-plan"
        project_plan["entities"] = [{"id": "Order"}]
        project_plan["api_contracts"].append(business())
        project_plan["agent_contracts"][0]["agentSettings"]["tools"] = {
            "enabled": True,
            "bindings": [{"toolId": "read_orders", "apiContractId": "order_api", "endpointId": "order.list"}],
        }
        project_plan = materialize_runtime_endpoints(project_plan)
        contract_id = "agent.order_assistant"
        endpoint_id = "agent.order_assistant.run"
        scope = {"type": "endpoint", "targetId": endpoint_id, "apiContractId": contract_id}
        build_context = {"target": {"type": "endpoint", "id": endpoint_id}, "endpoint_ids": [endpoint_id]}

        self.assertEqual(_scoped_contract_errors(project_plan, scope, build_context), [])

        runtime_contract = next(contract for contract in project_plan["api_contracts"] if contract["id"] == contract_id)
        runtime_contract["endpoints"][0]["path"] = "/forged"
        errors = _scoped_contract_errors(project_plan, scope, build_context)
        self.assertEqual(errors, [f"运行 Endpoint {contract_id} 的派生身份、绑定或能力已被修改/占用。"])

    def test_bodyless_post_command_is_allowed_when_semantics_use_path_and_auth(self) -> None:
        """命令型 POST 由路径参数和登录态表达完整语义时允许没有请求体。"""

        project_plan = {
            "entities": [{"id": "Photo", "name": "Photo", "fields": []}],
            "api_contracts": [
                {
                    "id": "photo_api",
                    "entity_ids": ["Photo"],
                    "schemas": {"PhotoActionOutput": {"type": "object"}},
                    "endpoints": [
                        {
                            "id": "photo_api.like",
                            "method": "POST",
                            "path": "/photos/{photoId}/like",
                            "parameters": [
                                {
                                    "name": "photoId",
                                    "in": "path",
                                    "required": True,
                                    "schema": {"type": "string"},
                                }
                            ],
                            "request_schema_ref": None,
                            "response_schema_ref": "PhotoActionOutput",
                            "authentication": {"required": True, "roles": ["user"]},
                        }
                    ],
                }
            ],
        }

        self.assertEqual(validate_api_contract_consistency(project_plan), [])

    def test_page_contract_endpoint_reference_does_not_require_data_source_dependency(self) -> None:
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
            "page_implementation_contracts": [
                {
                    "pageId": "orders",
                    "requiredEndpointIds": ["orders.list"],
                }
            ],
        }

        self.assertEqual(validate_api_contract_consistency(project_plan), [])

    def test_page_contract_unknown_endpoint_is_rejected(self) -> None:
        """endpoint 引用保留为页面唯一依赖时，未知 endpoint 仍必须被阻止。"""

        project_plan = {
            "entities": [],
            "api_contracts": [],
            "page_implementation_contracts": [
                {
                    "pageId": "orders",
                    "requiredEndpointIds": ["orders.list"],
                }
            ],
        }

        self.assertEqual(
            validate_api_contract_consistency(project_plan),
            ["Page orders: Endpoint 缺失或有歧义：/orders.list，请声明 apiContractId。"],
        )

    def test_contract_definition_validation_excludes_page_reference_errors(self) -> None:
        """页面引用错误不得被归类为 Contract-only 可修复错误。"""

        project_plan = {
            "entities": [],
            "api_contracts": [],
            "page_implementation_contracts": [
                {
                    "pageId": "name_entry",
                    "requiredEndpointIds": ["person_name_api.create"],
                }
            ],
        }

        self.assertEqual(validate_api_contract_definitions(project_plan), [])
        self.assertEqual(
            validate_api_contract_consistency(project_plan),
            [
                "Page name_entry: Endpoint 缺失或有歧义：/person_name_api.create，请声明 apiContractId。"
            ],
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
