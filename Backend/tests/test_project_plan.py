from __future__ import annotations

import unittest

from app.services.project_plan import create_project_plan
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

    def test_rendered_project_plan_includes_dependency_and_permission_sections(self) -> None:
        spec = create_requirement_spec("创建一个带登录权限的库存管理系统")
        plan = create_project_plan(spec)

        markdown = render_project_plan_markdown(plan)

        self.assertIn("## 需求概述", markdown)
        self.assertIn("## 整体需求验收标准", markdown)
        self.assertIn("## 页面与数据源依赖", markdown)
        self.assertIn("## 权限体系", markdown)

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
