from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.nodes.planning import detail_confirmation
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec


class DetailConfirmationTests(unittest.TestCase):
    def test_detail_confirmation_asks_user_to_select_target_first(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))

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

    def test_detail_confirmation_builds_page_spec_from_project_plan_context(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        initial = detail_confirmation(
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
                    "request": "页面信息正确，继续",
                    "project_plan": project_plan,
                    "selected_page_id": "inventory_management_list_page",
                    "page_spec_draft": initial["page_spec_draft"],
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
        self.assertEqual(result["clarification"]["mode"], "detail_target_selection")

    def test_detail_confirmation_asks_for_initial_page_spec_confirmation(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
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
        self.assertEqual(len(result["clarification"]["questions"]), 4)
        self.assertEqual(result["page_spec_draft"]["page_id"], "inventory_management_list_page")

    def test_detail_confirmation_can_confirm_data_source_target(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))

        result = detail_confirmation(
            {
                "request": "我选择 数据源：库存管理数据源 做详细设计",
                "project_plan": project_plan,
                "timeline": [],
            }
        )

        self.assertEqual(result["selected_data_source_id"], "")
        self.assertEqual(result["detail_plans"][0]["type"], "data_source")
        self.assertEqual(
            result["project_plan"]["data_source_detail_plans"][0]["data_source_id"],
            "inventory_management_source",
        )
        self.assertEqual(
            result["detail_selection"]["previous_target"]["id"],
            "inventory_management_source",
        )

    def test_detail_confirmation_promotes_pending_plan_after_user_confirmation(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
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
