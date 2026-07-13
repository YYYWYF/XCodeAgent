from __future__ import annotations

import unittest

from app.services.project_plan import create_project_plan
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.page_detail_plan import (
    create_page_detail_plan,
    create_page_spec_from_project_plan,
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
        self.assertIn("page_data_dependencies", plan)
        self.assertIn("permission_model", plan)
        self.assertIn("task_inputs", plan)
        self.assertTrue(plan["page_data_dependencies"])
        self.assertTrue(plan["permission_model"]["page_access"])
        self.assertTrue(plan["project_acceptance_criteria"])
        self.assertTrue(plan["task_inputs"]["frontend"])
        self.assertTrue(plan["task_inputs"]["data_source"])

    def test_project_plan_merges_main_agent_json_sections(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        agent_plan = {
            "architecture": {
                "frontend": "React 18 + Ant Design operational console.",
            },
            "frontend_pages": [
                {
                    "id": "dashboard_page",
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
            "id": "inventory_only",
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

        self.assertEqual([page["id"] for page in plan["frontend_pages"]], ["inventory_only"])

    def test_coordination_plan_missing_outputs_is_normalized_and_renderable(self) -> None:
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

        self.assertTrue(plan["coordination_plan"]["detail_confirmation"]["outputs"])
        self.assertIn("逐项确认页面和数据源", render_project_plan_markdown(plan))

    def test_project_plan_tolerates_requirement_page_without_description(self) -> None:
        spec = create_requirement_spec("创建一个人员管理系统")
        spec["pages"] = [
            {
                "id": "people_list",
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
        self.assertIn("## 页面与数据源依赖", markdown)
        self.assertIn("## 权限体系", markdown)

    def test_project_plan_generates_complete_api_endpoint_contracts(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")

        plan = create_project_plan(spec)
        dependency = next(
            item
            for item in plan["page_data_dependencies"]
            if item["endpoint_dependencies"]
        )
        contract = next(
            item
            for item in plan["api_contracts"]
            if item["id"] == dependency["api_contract_ids"][0]
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
        self.assertTrue(dependency["endpoint_dependencies"])
        self.assertEqual(
            dependency["endpoint_dependencies"][0]["api_contract_id"],
            contract["id"],
        )

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

    def test_contract_consistency_rejects_fields_defined_by_data_source(self) -> None:
        plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        plan["data_sources"][0]["schema"] = {"unexpected": "field"}

        errors = validate_api_contract_consistency(plan)

        self.assertTrue(any("duplicates contract fields" in error for error in errors))

    def test_contract_consistency_rejects_unknown_page_response_field(self) -> None:
        plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        page_spec = create_page_spec_from_project_plan(
            plan,
            "inventory_management_list_page",
        )
        page_detail = create_page_detail_plan(
            plan,
            page_spec,
            agent_detail_plan={
                "response_bindings": [
                    {
                        "endpoint_id": "inventory_management_source_api.list",
                        "source_path": "items[].field_not_in_contract",
                        "page_field": "invalid",
                    }
                ]
            },
        )
        plan["page_detail_plans"] = [page_detail]

        errors = validate_api_contract_consistency(plan)

        self.assertTrue(any("unknown response field" in error for error in errors))

    def test_contract_consistency_accepts_jsonpath_list_response_bindings(self) -> None:
        plan = create_project_plan(create_requirement_spec("创建一个人员管理系统"))
        page_dependency = next(
            dependency
            for dependency in plan["page_data_dependencies"]
            if dependency.get("endpoint_dependencies")
            and str(
                dependency["endpoint_dependencies"][0].get("endpoint_id", "")
            ).endswith(".list")
        )
        page_id = page_dependency["page_id"]
        endpoint_id = page_dependency["endpoint_dependencies"][0]["endpoint_id"]
        page_spec = create_page_spec_from_project_plan(
            plan,
            page_id,
        )
        page_detail = create_page_detail_plan(
            plan,
            page_spec,
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
                    "id": "dashboard_page",
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
                    "page_id": "dashboard_page",
                    "data_source_ids": "inventory_source",
                    "api_contract_ids": "inventory_source_api",
                },
                "依赖说明",
            ],
        }

        plan = create_project_plan(spec, agent_plan=agent_plan)
        markdown = render_project_plan_markdown(plan)

        self.assertIsInstance(plan["frontend_pages"][0]["data_dependencies"], list)
        self.assertTrue(plan["frontend_pages"][0]["permissions"])
        self.assertIsInstance(plan["api_contracts"][0]["endpoints"], list)
        self.assertIn("## API 契约", markdown)


if __name__ == "__main__":
    unittest.main()
