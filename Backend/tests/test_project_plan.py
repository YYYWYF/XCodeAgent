from __future__ import annotations

import unittest

from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.api_contracts import response_field_paths
from app.services.page_dependencies import validate_project_plan_dependencies
from app.services.page_detail_plan import (
    create_page_detail_plan,
    extract_page_detail_context,
)
from app.services.project_plan import create_project_plan, normalize_project_plan
from app.services.requirement_spec import create_requirement_spec
from app.workspace.plan_documents import render_project_plan_markdown


class ProjectPlanTests(unittest.TestCase):
    def test_project_plan_contains_project_level_sections(self) -> None:
        spec = create_requirement_spec("创建一个带登录权限的库存管理系统")

        plan = create_project_plan(spec)

        self.assertIn("requirements_overview", plan)
        self.assertIn("project_acceptance_criteria", plan)
        self.assertIn("architecture", plan)
        self.assertIn("api_contracts", plan)
        self.assertIn("frontend_pages", plan)
        self.assertIn("data_sources", plan)
        self.assertIn("permission_model", plan)
        self.assertNotIn("task_inputs", plan)
        self.assertTrue(plan["frontend_pages"][0]["references"]["endpoint_dependencies"])
        self.assertTrue(plan["permission_model"]["page_access"])
        self.assertTrue(plan["project_acceptance_criteria"])
        self.assertTrue(plan["data_sources"])

    def test_project_plan_merges_main_agent_json_sections(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        agent_plan = {
            "architecture": {
                "frontend": "React 18 + Ant Design operational console.",
            },
            "frontend_pages": [
                {
                    "pageId": "dashboard_page",
                    "description": "Main Agent refined dashboard.",
                }
            ],
            "permission_model": {
                "default_policy": "role_based_allowlist",
            },
            "project_acceptance_criteria": [
                "库存管理系统核心流程通过端到端验收。",
            ],
        }

        plan = create_project_plan(spec, agent_plan=agent_plan)

        self.assertTrue(plan["agent_plan_used"])
        self.assertEqual(
            plan["architecture"]["frontend"],
            "React 18 + Ant Design operational console.",
        )
        self.assertEqual(
            plan["frontend_pages"][0]["description"],
            "Main Agent refined dashboard.",
        )
        self.assertEqual(
            plan["permission_model"]["default_policy"],
            "role_based_allowlist",
        )
        self.assertEqual(
            plan["project_acceptance_criteria"],
            ["库存管理系统核心流程通过端到端验收。"],
        )

    def test_authoritative_agent_plan_can_replace_page_and_role_scoped_content(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        only_page = {
            "pageId": "inventory_only",
            "name": "库存列表",
            "path": "/inventory",
            "module_id": "inventory_management",
            "description": "唯一业务页面",
            "data_dependencies": [],
            "states": ["ready"],
            "permissions": ["user"],
        }

        plan = create_project_plan(
            spec,
            agent_plan={"frontend_pages": [only_page]},
            authoritative_agent_plan=True,
        )

        self.assertEqual([page["pageId"] for page in plan["frontend_pages"]], ["inventory_only"])

    def test_agent_pages_without_paths_get_unique_pageId_routes(self) -> None:
        spec = create_requirement_spec("创建一个人员管理系统")

        plan = create_project_plan(
            spec,
            agent_plan={
                "frontend_pages": [
                    {"pageId": "dashboard_page", "name": "仪表盘"},
                    {"pageId": "employees_list", "name": "员工列表"},
                    {"pageId": "onboarding_form", "name": "入职表单"},
                ]
            },
            authoritative_agent_plan=True,
        )

        paths = [page["path"] for page in plan["frontend_pages"]]
        self.assertEqual(paths, ["/", "/employees-list", "/onboarding-form"])

    def test_requirement_pages_with_duplicate_root_paths_get_unique_routes(self) -> None:
        spec = create_requirement_spec(
            "创建一个人员管理系统",
            agent_spec={
                "pages": [
                    {"pageId": "dashboard_page", "name": "仪表盘", "path": "/"},
                    {"pageId": "employees_list", "name": "员工列表", "path": "/"},
                    {"pageId": "offboarding_form", "name": "离职表单", "path": "/"},
                ]
            },
            authoritative_agent_spec=True,
        )

        plan = create_project_plan(spec)

        self.assertEqual(
            [page["path"] for page in plan["frontend_pages"]],
            ["/", "/employees-list", "/offboarding-form"],
        )

    def test_requirement_spec_does_not_emit_duplicate_root_page_paths(self) -> None:
        spec = create_requirement_spec(
            "创建一个人员管理系统",
            agent_spec={
                "pages": [
                    {"pageId": "dashboard_page", "name": "仪表盘", "path": "/"},
                    {"pageId": "employees_list", "name": "员工列表", "path": "/"},
                    {"pageId": "onboarding_form", "name": "入职表单", "path": "/"},
                ]
            },
            authoritative_agent_spec=True,
        )

        self.assertEqual(
            [page["path"] for page in spec["pages"]],
            ["/", "/employees-list", "/onboarding-form"],
        )

    def test_project_plan_with_coordination_note_is_renderable(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(
            spec,
            agent_plan={
                "coordination_plan": {
                    "detail_confirmation": {
                        "strategy": "逐项确认页面和数据源。",
                    }
                }
            },
            authoritative_agent_plan=True,
        )

        self.assertIn("库存管理应用总体计划书", render_project_plan_markdown(plan))

    def test_project_plan_tolerates_requirement_page_without_description(self) -> None:
        spec = create_requirement_spec("创建一个人员管理系统")
        spec["pages"] = [
            {
                "pageId": "people_list",
                "name": "人员列表",
                "path": "/people",
                "module_id": "people",
            }
        ]

        plan = create_project_plan(spec)
        markdown = render_project_plan_markdown(plan)

        self.assertEqual(plan["frontend_pages"][0]["description"], "人员列表")
        self.assertIn("人员列表", markdown)

    def test_rendered_project_plan_includes_dependency_and_permission_sections(self) -> None:
        spec = create_requirement_spec("创建一个带登录权限的库存管理系统")
        plan = create_project_plan(spec)

        markdown = render_project_plan_markdown(plan)

        self.assertIn("## 需求概述", markdown)
        self.assertIn("## 整体需求验收标准", markdown)
        self.assertIn("## 前端页面清单", markdown)
        self.assertIn("endpoint_dependencies", markdown)
        self.assertIn("## 权限体系", markdown)

    def test_project_plan_generates_complete_api_endpoint_contracts(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")

        plan = create_project_plan(spec)
        endpoint_dependency = next(
            dependency
            for page in plan["frontend_pages"]
            for dependency in page["references"]["endpoint_dependencies"]
            if dependency.get("endpoint_id")
        )
        contract = next(
            contract
            for contract in plan["api_contracts"]
            if any(
                endpoint.get("id") == endpoint_dependency["endpoint_id"]
                for endpoint in contract["endpoints"]
            )
        )
        endpoint = contract["endpoints"][0]

        self.assertTrue(endpoint["id"])
        self.assertTrue(endpoint["path"].startswith("/api/"))
        self.assertIsInstance(endpoint["parameters"], list)
        self.assertIn(endpoint["response_schema_ref"], contract["schemas"])
        create_endpoint = next(
            candidate
            for candidate in contract["endpoints"]
            if candidate["id"].endswith(".create")
        )
        self.assertIn(create_endpoint["request_schema_ref"], contract["schemas"])
        self.assertNotIn("schema", plan["data_sources"][0])
        self.assertTrue(plan["data_sources"][0]["schema_refs"])
        self.assertTrue(endpoint_dependency["endpoint_id"])
        self.assertTrue(any(endpoint["id"] == endpoint_dependency["endpoint_id"] for endpoint in contract["endpoints"]))

    def test_project_plan_normalizes_agent_endpoint_contract_shapes(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(
            spec,
            agent_plan={
                "api_contracts": [
                    {
                        "id": "inventory_api",
                        "data_source_id": "inventory_management_source",
                        "resource": "Inventory",
                        "base_path": "/api/inventory",
                        "schemas": {
                            "Inventory": {
                                "type": "object",
                                "properties": {"id": {"type": "string"}},
                                "required": ["id"],
                            }
                        },
                        "endpoints": [
                            {
                                "id": "inventory.search",
                                "method": "get",
                                "path": "/api/inventory",
                                "response_schema_ref": "Inventory",
                            }
                        ],
                    }
                ]
            },
            authoritative_agent_plan=True,
        )

        endpoint = plan["api_contracts"][0]["endpoints"][0]
        self.assertEqual(endpoint["method"], "GET")
        self.assertEqual(endpoint["path"], "/api/inventory")
        self.assertEqual(endpoint["response_schema_ref"], "Inventory")
        self.assertEqual(validate_api_contract_consistency(plan), [])

    def test_project_plan_accepts_contract_id_alias_without_losing_contract(self) -> None:
        """模型使用 contract_id 时仍保留契约并补齐唯一数据源关联。"""

        spec = create_requirement_spec("创建一个天气预报系统")
        source_id = "weather_source"
        plan = create_project_plan(
            spec,
            agent_plan={
                "data_sources": [
                    {
                        "id": source_id,
                        "name": "天气数据源",
                        "type": "api",
                        "entities": ["Weather"],
                        "schema_refs": ["Weather"],
                    }
                ],
                "api_contracts": [
                    {
                        "contract_id": "weather_contract",
                        "schemas": {
                            "Weather": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                                "required": ["city"],
                            }
                        },
                        "endpoints": [
                            {
                                "id": "weather.get",
                                "method": "GET",
                                "path": "/api/weather",
                                "response_schema_ref": "Weather",
                            }
                        ],
                    }
                ]
            },
            authoritative_agent_plan=True,
        )

        self.assertEqual(len(plan["api_contracts"]), 1)
        self.assertEqual(plan["api_contracts"][0]["id"], "weather_contract")
        self.assertEqual(plan["api_contracts"][0]["data_source_id"], source_id)
        self.assertNotIn("contract_id", plan["api_contracts"][0])
        self.assertEqual(validate_api_contract_consistency(plan), [])

    def test_project_plan_keeps_default_contract_when_model_returns_empty_list(self) -> None:
        """业务数据源存在时不允许模型空数组删除确定性默认契约。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(
            spec,
            agent_plan={"api_contracts": []},
            authoritative_agent_plan=True,
        )

        self.assertTrue(plan["api_contracts"])
        self.assertEqual(validate_api_contract_consistency(plan), [])

    def test_contract_consistency_rejects_empty_contracts_for_data_sources(self) -> None:
        """数据源存在但契约为空时在用户确认前返回明确错误。"""

        errors = validate_api_contract_consistency(
            {
                "api_contracts": [],
                "data_sources": [{"id": "weather_source", "schema_refs": []}],
            }
        )

        self.assertIn(
            "ProjectPlan defines data sources but api_contracts is empty.",
            errors,
        )
        self.assertIn(
            "ProjectPlan defines data sources but api_contracts is empty.",
            validate_project_plan_dependencies(
                {
                    "api_contracts": [],
                    "data_sources": [{"id": "weather_source"}],
                    "frontend_pages": [],
                }
            ),
        )

    def test_project_plan_normalizes_json_pointer_schema_refs(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(
            spec,
            agent_plan={
                "api_contracts": [
                    {
                        "id": "inventory_api",
                        "data_source_id": "inventory_management_source",
                        "schemas": {
                            "Inventory": {
                                "type": "object",
                                "properties": {"id": {"type": "string"}},
                            },
                            "InventoryList": {
                                "type": "object",
                                "properties": {
                                    "items": {
                                        "type": "array",
                                        "items": {"$ref": "#/schemas/Inventory"},
                                    }
                                },
                            },
                        },
                        "endpoints": [
                            {
                                "id": "inventory.list",
                                "method": "GET",
                                "path": "/api/inventory",
                                "response_schema_ref": "#/schemas/InventoryList",
                            }
                        ],
                    }
                ]
            },
            authoritative_agent_plan=True,
        )

        contract = plan["api_contracts"][0]
        self.assertEqual(
            contract["schemas"]["InventoryList"]["properties"]["items"]["items"][
                "$ref"
            ],
            "Inventory",
        )
        self.assertEqual(
            contract["endpoints"][0]["response_schema_ref"],
            "InventoryList",
        )
        self.assertEqual(validate_api_contract_consistency(plan), [])

    def test_normalize_project_plan_only_normalizes_api_contracts(self) -> None:
        plan = {
            "api_contracts": [
                {
                    "id": "inventory_api",
                    "data_source_id": "inventory_source",
                    "resource": "Inventory",
                    "base_path": "/api/inventory",
                    "schemas": {
                        "Inventory": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                        },
                        "InventoryList": {
                            "type": "object",
                            "properties": {
                                "items": {
                                    "type": "array",
                                    "items": {"$ref": "#/schemas/Inventory"},
                                }
                            },
                        },
                    },
                    "endpoints": [
                        {
                            "id": "inventory.list",
                            "method": "get",
                            "path": "/api/inventory",
                            "response_schema_ref": "#/schemas/InventoryList",
                        }
                    ],
                }
            ],
            "frontend_pages": [{"pageId": "inventory_page"}],
        }

        normalized = normalize_project_plan(plan)

        contract = normalized["api_contracts"][0]
        self.assertEqual(
            contract["schemas"]["InventoryList"]["properties"]["items"]["items"][
                "$ref"
            ],
            "Inventory",
        )
        self.assertEqual(
            contract["endpoints"][0]["response_schema_ref"],
            "InventoryList",
        )
        self.assertEqual(normalized["frontend_pages"], plan["frontend_pages"])

    def test_legacy_json_pointer_refs_validate_and_expand_all_of_paths(self) -> None:
        plan = {
            "api_contracts": [
                {
                    "id": "app_api",
                    "schemas": {
                        "Item": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                        },
                        "Detail": {
                            "allOf": [
                                {"$ref": "#/schemas/Item"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "relatedItems": {
                                            "type": "array",
                                            "items": {"$ref": "#/schemas/Item"},
                                        }
                                    },
                                },
                            ]
                        },
                    },
                    "endpoints": [
                        {
                            "id": "getDetail",
                            "method": "GET",
                            "path": "/api/detail",
                            "response_schema_ref": "#/schemas/Detail",
                        }
                    ],
                }
            ],
            "data_sources": [],
            "page_detail_plans": [],
        }

        self.assertEqual(validate_api_contract_consistency(plan), [])
        self.assertEqual(
            set(response_field_paths(plan["api_contracts"], "getDetail")),
            {"id", "relatedItems", "relatedItems[].id"},
        )

        plan["api_contracts"][0]["endpoints"][0][
            "response_schema_ref"
        ] = "other_api#/schemas/Detail"
        self.assertTrue(
            any(
                "unknown schema other_api#/schemas/Detail" in error
                for error in validate_api_contract_consistency(plan)
            )
        )

    def test_contract_consistency_rejects_fields_defined_by_data_source(self) -> None:
        plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        plan["data_sources"][0]["schema"] = {"unexpected": "field"}

        errors = validate_api_contract_consistency(plan)

        self.assertTrue(any("duplicates contract fields" in error for error in errors))

    def test_contract_consistency_rejects_unknown_page_response_field(self) -> None:
        plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        page_context = extract_page_detail_context(
            plan,
            "inventory_management_list_page",
        )
        page_detail = create_page_detail_plan(
            plan,
            page_context,
        )
        page_detail["response_bindings"] = [
            {
                "endpoint_id": "inventory_management_source_api.list",
                "source_path": "items[].field_not_in_contract",
                "page_field": "invalid",
            }
        ]
        plan["page_detail_plans"] = [page_detail]

        errors = validate_api_contract_consistency(plan)

        self.assertTrue(any("unknown response field" in error for error in errors))

    def test_contract_consistency_accepts_jsonpath_list_response_bindings(self) -> None:
        plan = create_project_plan(create_requirement_spec("创建一个人员管理系统"))
        page = next(
            page
            for page in plan["frontend_pages"]
            if any(
                str(dependency.get("endpoint_id", "")).endswith(".list")
                for dependency in page["references"]["endpoint_dependencies"]
            )
        )
        pageId = page["pageId"]
        endpoint_id = next(
            dependency["endpoint_id"]
            for dependency in page["references"]["endpoint_dependencies"]
            if str(dependency.get("endpoint_id", "")).endswith(".list")
        )
        page_context = extract_page_detail_context(
            plan,
            pageId,
        )
        page_detail = create_page_detail_plan(
            plan,
            page_context,
            agent_detail_plan={
                "response_bindings": [
                    {
                        "endpoint_id": endpoint_id,
                        "source_path": "$.items.",
                        "page_field": "items",
                    },
                    {
                        "endpoint_id": endpoint_id,
                        "source_path": "$.total",
                        "page_field": "total",
                    },
                ]
            },
        )
        plan["page_detail_plans"] = [page_detail]

        self.assertEqual(validate_api_contract_consistency(plan), [])
        self.assertEqual(page_detail["response_bindings"][0]["source_path"], "items")

    def test_project_plan_tolerates_agent_string_items(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        agent_plan = {
            "requirements_overview": {
                "roles": ["库管员", "管理员"],
                "modules": ["库存管理"],
                "business_flows": ["入库到库存更新"],
            },
            "frontend_pages": [
                {
                    "pageId": "dashboard_page",
                    "data_dependencies": "inventory_source",
                    "permissions": "admin",
                },
                "页面清单说明",
            ],
            "api_contracts": [
                {
                    "id": "inventory_source_api",
                    "endpoints": ["GET /api/inventory"],
                },
                "API 契约说明",
            ],
            "page_data_dependencies": [
                {
                    "pageId": "dashboard_page",
                    "data_source_ids": "inventory_source",
                    "api_contract_ids": "inventory_source_api",
                },
                "依赖说明",
            ],
        }

        plan = create_project_plan(spec, agent_plan=agent_plan)
        markdown = render_project_plan_markdown(plan)

        self.assertIsInstance(plan["frontend_pages"][0]["references"]["endpoint_dependencies"], list)
        self.assertTrue(plan["frontend_pages"][0]["references"]["permissions"])
        self.assertIsInstance(plan["api_contracts"][0]["endpoints"], list)
        self.assertIn("## API 契约", markdown)


if __name__ == "__main__":
    unittest.main()
