from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.nodes.planning import detail_confirmation
from app.services.page_detail_plan import (
    create_data_source_detail_plan,
    create_page_detail_plan,
    create_page_spec_from_project_plan,
)
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec
from app.workspace.plan_documents import render_project_plan_markdown


class DetailConfirmationTests(unittest.TestCase):
    def test_model_json_overrides_page_and_data_source_detail_fields(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        page_spec = create_page_spec_from_project_plan(
            project_plan,
            "inventory_management_list_page",
        )
        page_detail = create_page_detail_plan(
            project_plan,
            page_spec,
            agent_detail_plan={"interactions": ["仅查看，不允许删除"]},
        )
        data_detail = create_data_source_detail_plan(
            project_plan,
            "inventory_management_source",
            agent_detail_plan={"schema": {"fields": [{"name": "sku", "unique": True}]}},
        )

        self.assertEqual(page_detail["interactions"], ["仅查看，不允许删除"])
        self.assertNotIn("schema", data_detail)
        self.assertTrue(data_detail["schema_refs"])

    def test_page_spec_defaults_when_page_description_is_missing(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个人员管理系统")
        )
        project_plan["frontend_pages"] = [
            {
                "id": "people_list",
                "name": "人员列表",
                "path": "/people",
                "data_dependencies": [],
                "permissions": ["user"],
            }
        ]

        page_spec = create_page_spec_from_project_plan(project_plan, "people_list")

        self.assertEqual(page_spec["page_id"], "people_list")
        self.assertIn("人员列表", page_spec["page_goal"])

    def test_page_detail_tolerates_api_contract_without_data_source_id(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        for contract in project_plan["api_contracts"]:
            contract.pop("data_source_id", None)

        page_spec = create_page_spec_from_project_plan(
            project_plan,
            "inventory_management_list_page",
        )
        page_detail = create_page_detail_plan(project_plan, page_spec)

        self.assertTrue(page_spec["api_contract_ids"])
        self.assertEqual(
            page_detail["data_sources"][0]["id"],
            "inventory_management_source",
        )

    def test_page_detail_tolerates_structured_layout_and_interactions(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        page_spec = create_page_spec_from_project_plan(
            project_plan,
            "inventory_management_list_page",
        )

        page_detail = create_page_detail_plan(
            project_plan,
            page_spec,
            agent_detail_plan={
                "basic_layout": {
                    "structure": [
                        {"name": "筛选区", "description": "按库存状态筛选"},
                    ],
                },
                "interactions": [
                    {"name": "搜索", "description": "按 SKU 搜索库存"},
                ],
            },
        )
        project_plan["page_detail_plans"] = [page_detail]

        markdown = render_project_plan_markdown(project_plan)

        self.assertIn("筛选区", markdown)
        self.assertIn("搜索", markdown)

    def test_page_detail_only_carries_the_page_endpoint_dependencies(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        page_spec = create_page_spec_from_project_plan(
            project_plan,
            "inventory_management_list_page",
        )
        page_spec["page_dependencies"]["endpoint_dependencies"] = [
            {
                "api_contract_id": "inventory_management_source_api",
                "endpoint_id": "inventory_management_source_api.list",
                "method": "GET",
                "url": "/api/inventory-management",
                "usage": "page_load",
                "required": True,
            }
        ]

        page_detail = create_page_detail_plan(project_plan, page_spec)

        endpoints = page_detail["data_sources"][0]["endpoints"]
        self.assertEqual([endpoint["id"] for endpoint in endpoints], [
            "inventory_management_source_api.list"
        ])

    def test_detail_confirmation_asks_user_to_select_target_first(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )

        result = detail_confirmation(
            {
                "request": "创建一个库存管理系统",
                "project_plan": project_plan,
                "timeline": [],
            }
        )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["phase"], "detail_confirmation")
        self.assertEqual(result["clarification"]["status"], "requires_user_input")
        self.assertTrue(result["detail_selection"]["targets"])

    def test_detail_confirmation_builds_page_spec_from_project_plan_context(
        self,
    ) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        selection = detail_confirmation(
            {
                "request": "我选择 页面：库存管理列表页 做详细设计",
                "project_plan": project_plan,
                "timeline": [],
            }
        )

        with patch(
            "app.graph.nodes.planning.design_page_with_chat_model",
            side_effect=lambda _plan, spec: {
                "id": f"page_detail:{spec['page_id']}",
                "page_id": spec["page_id"],
                "page_name": spec["page_name"],
                "path": spec["path"],
                "status": "confirmed",
                "source_page_spec": spec,
            },
        ) as designer:
            result = detail_confirmation(
                {
                    "request": "- 页面目标\n  回答：展示库存并支持筛选",
                    "project_plan": project_plan,
                    "selected_page_id": selection["selected_page_id"],
                    "page_spec_draft": selection["page_spec_draft"],
                    "timeline": [],
                }
            )

        spec = result["page_spec_confirmation"]["confirmed_page_spec"]
        designer.assert_called_once()
        self.assertEqual(spec["page_id"], "inventory_management_list_page")
        self.assertTrue(spec["data_source_ids"])
        self.assertTrue(spec["api_contract_ids"])
        self.assertIn("page_dependencies", spec)
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["project_plan"]["page_detail_plans"][0]["page_id"],
            spec["page_id"],
        )

    def test_detail_confirmation_asks_for_missing_page_spec_aspects(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        for page in project_plan["frontend_pages"]:
            if page["id"] == "inventory_management_list_page":
                page["permissions"] = []

        result = detail_confirmation(
            {
                "request": "我选择 页面：库存管理列表页 做详细设计",
                "project_plan": project_plan,
                "timeline": [],
            }
        )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "page_spec_confirmation")
        self.assertEqual(
            result["page_spec_draft"]["page_id"], "inventory_management_list_page"
        )

    def test_detail_confirmation_can_confirm_data_source_target(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )

        selection = detail_confirmation(
            {
                "request": "我选择 数据源：库存管理数据源 做详细设计",
                "project_plan": project_plan,
                "timeline": [],
            }
        )

        self.assertEqual(
            selection["selected_data_source_id"], "inventory_management_source"
        )
        self.assertEqual(selection["clarification"]["mode"], "data_source_spec_confirmation")

        with patch(
            "app.graph.nodes.planning.design_data_source_with_chat_model",
            return_value={
                "id": "data_source_detail:inventory_management_source",
                "type": "data_source",
                "data_source_id": "inventory_management_source",
                "data_source_name": "库存管理数据源",
                "status": "confirmed",
                "schema": {"fields": [{"name": "sku", "unique": True}]},
            },
        ):
            result = detail_confirmation(
                {
                    "request": "- 数据源设计\n  回答：sku 字段必须唯一",
                    "project_plan": project_plan,
                    "selected_data_source_id": selection["selected_data_source_id"],
                    "data_source_spec_draft": selection["data_source_spec_draft"],
                    "timeline": [],
                }
            )

        self.assertEqual(
            result["project_plan"]["data_source_detail_plans"][0][
                "data_source_id"
            ],
            "inventory_management_source",
        )

    def test_detail_confirmation_promotes_pending_plan_after_user_confirmation(
        self,
    ) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        pending_project_plan = {
            **project_plan,
            "page_detail_plans": [
                {
                    "id": "page_detail:inventory_management_list_page",
                    "page_id": "inventory_management_list_page",
                }
            ],
        }

        result = detail_confirmation(
            {
                "request": "正确，继续",
                "project_plan": project_plan,
                "pending_project_plan": pending_project_plan,
                "timeline": [],
            }
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["project_plan"]["confirmation_status"], "confirmed")
        self.assertEqual(
            result["project_plan"]["page_detail_plans"][0]["page_id"],
            "inventory_management_list_page",
        )


if __name__ == "__main__":
    unittest.main()
