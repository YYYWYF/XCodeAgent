from __future__ import annotations

import unittest

from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.api_contracts import response_field_paths
from app.services.frontend_page_tree import (
    apply_frontend_page_route_hierarchy,
    flatten_frontend_pages,
)
from app.services.page_dependencies import validate_project_plan_dependencies
from app.services.page_detail_plan import (
    create_page_detail_plan,
    extract_page_detail_context,
)
from app.services.application_planning_persistence import project_plan_application_payload
from app.services.project_plan import (
    create_project_plan,
    normalize_project_plan,
    validate_project_plan_datasource_policy,
)
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

    def test_project_acceptance_excludes_xcodeagent_workflow_gates(self) -> None:
        """ProjectPlan 产品验收不得混入 XCodeAgent 测试和流程门禁。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(
            spec,
            agent_plan={
                "project_acceptance_criteria": [
                    "集成测试和质量门禁通过后才进入用户验收。",
                    "仓库管理员可以查看库存并完成库存调整。",
                ]
            },
        )

        self.assertEqual(
            plan["project_acceptance_criteria"],
            ["仓库管理员可以查看库存并完成库存调整。"],
        )

    def test_authoritative_agent_plan_cannot_replace_requirement_pages(self) -> None:
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

        self.assertEqual(
            [page["pageId"] for page in plan["frontend_pages"]],
            [page["pageId"] for page in spec["pages"]],
        )
        self.assertNotIn("inventory_only", [page["pageId"] for page in plan["frontend_pages"]])

    def test_agent_cannot_add_pages_outside_requirement_spec(self) -> None:
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

        baseline = create_project_plan(spec)
        self.assertEqual(
            [page["pageId"] for page in plan["frontend_pages"]],
            [page["pageId"] for page in baseline["frontend_pages"]],
        )
        self.assertEqual(
            [page["path"] for page in plan["frontend_pages"]],
            [page["path"] for page in baseline["frontend_pages"]],
        )

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

    def test_menu_routes_extend_from_root_and_page_routes_extend_from_menu(self) -> None:
        routed = apply_frontend_page_route_hierarchy(
            [
                {
                    "name": "管理中心",
                    "unique_path": "/management",
                    "children": [
                        {
                            "pageId": "role_page",
                            "name": "角色管理",
                            "path": "/role",
                            "module_id": "access_control",
                            "description": "角色管理页面",
                        },
                        {
                            "pageId": "resource_page",
                            "name": "资源管理",
                            "path": "/resource",
                            "module_id": "access_control",
                            "description": "资源管理页面",
                        },
                    ],
                }
            ],
            root_route_prefix="/root",
        )

        self.assertEqual(routed[0]["unique_path"], "/root/management")
        self.assertEqual(routed[0]["children"][0]["path"], "/root/management/role")
        self.assertEqual(routed[0]["children"][1]["path"], "/root/management/resource")

    def test_empty_menu_route_keeps_pages_directly_under_root_prefix(self) -> None:
        routed = apply_frontend_page_route_hierarchy(
            [
                {
                    "name": "管理中心",
                    "unique_path": "",
                    "children": [
                        {
                            "pageId": "role_page",
                            "name": "角色管理",
                            "path": "/role",
                            "module_id": "access_control",
                            "description": "角色管理页面",
                        }
                    ],
                }
            ],
            root_route_prefix="/root",
        )

        self.assertEqual(routed[0]["unique_path"], "")
        self.assertEqual(routed[0]["children"][0]["path"], "/root/role")

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

    def test_project_plan_generates_required_api_endpoint_contracts(self) -> None:
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
        self.assertLess(len(contract["endpoints"]), 5)
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
        source_id = spec["data_sources"][0]["id"]
        plan = create_project_plan(
            spec,
            agent_plan={
                "data_sources": [
                    {
                        "id": source_id,
                        "name": "天气数据源",
                        "type": "database",
                        "entities": ["Weather"],
                        "schema_refs": ["Weather"],
                    }
                ],
                "api_contracts": [
                    {
                        "contract_id": "weather_contract",
                        "data_source_id": source_id,
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

    def test_normalize_project_plan_normalizes_api_contracts_and_page_leaves(self) -> None:
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
        normalized_pages = flatten_frontend_pages(normalized["frontend_pages"])
        self.assertEqual(normalized_pages[0]["pageId"], "inventory_page")
        self.assertEqual(normalized_pages[0]["path"], "/inventory")

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
        self.assertIn("## 真实 HTTP API 契约", markdown)

    def test_static_project_plan_keeps_contracts_without_database_stack(self) -> None:
        """Static 计划保留逻辑契约，但不得声明数据库实现。"""

        spec = create_requirement_spec("创建一个库存查看系统", datasource_type="static")
        plan = create_project_plan(
            spec,
            datasource_type="static",
            agent_plan={
                "architecture": {
                    "orm": "MyBatis",
                    "migration": "Flyway database migration",
                }
            },
        )
        markdown = render_project_plan_markdown(plan)

        self.assertTrue(plan["api_contracts"])
        self.assertEqual(plan["data_sources"][0]["type"], "static")
        self.assertTrue(plan["data_sources"][0]["description"])
        self.assertNotIn("database", plan["architecture"]["backend_tech_stack"])
        self.assertNotIn("cache", plan["architecture"]["backend_tech_stack"])
        self.assertNotIn("orm", plan["architecture"])
        self.assertNotIn("migration", plan["architecture"])
        self.assertIn("## 前端 Mock 数据契约", markdown)
        self.assertEqual(validate_project_plan_datasource_policy(plan, "static"), [])

    def test_database_project_plan_uses_real_http_mysql_and_redis(self) -> None:
        """Database 计划固定真实 HTTP、MySQL8 和 Redis。"""

        spec = create_requirement_spec("创建订单管理系统", datasource_type="database")
        plan = create_project_plan(spec, datasource_type="database")

        self.assertEqual(plan["architecture"]["data_contract"], "真实 HTTP API 契约。")
        self.assertEqual(plan["architecture"]["backend_tech_stack"]["database"], "MySQL8")
        self.assertEqual(plan["architecture"]["backend_tech_stack"]["cache"], "Redis")
        self.assertEqual(validate_project_plan_datasource_policy(plan, "database"), [])

    def test_project_plan_preserves_requirement_datasource_business_fields(self) -> None:
        """模型只能补充规划字段，不能覆盖需求数据源业务字段。"""

        spec = create_requirement_spec("创建订单管理系统", datasource_type="static")
        source = spec["data_sources"][0]
        plan = create_project_plan(
            spec,
            datasource_type="static",
            authoritative_agent_plan=True,
            agent_plan={
                "data_sources": [
                    {
                        "id": source["id"],
                        "name": "模型改名",
                        "description": "模型改写描述",
                        "entities": ["ChangedEntity"],
                        "type": "database",
                        "seed_strategy": "model_seed",
                    }
                ]
            },
        )

        planned = plan["data_sources"][0]
        for key in ("id", "name", "description", "entities", "type"):
            self.assertEqual(planned[key], source[key])
        self.assertEqual(planned["seed_strategy"], "model_seed")

    def test_application_projection_rejects_mock_and_keeps_static_description(self) -> None:
        """application.json 投影只写正式类型并保留数据源描述。"""

        spec = create_requirement_spec("创建库存查看系统", datasource_type="static")
        plan = create_project_plan(spec, datasource_type="static")
        payload = project_plan_application_payload(plan)
        self.assertEqual(payload["dataSources"][0]["type"], "static")
        self.assertEqual(
            payload["dataSources"][0]["description"],
            plan["data_sources"][0]["description"],
        )
        plan["data_sources"][0]["type"] = "mock"
        with self.assertRaises(ValueError):
            project_plan_application_payload(plan)


if __name__ == "__main__":
    unittest.main()
