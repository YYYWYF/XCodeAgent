from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
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

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.planning.design_page_with_main_agent",
                side_effect=lambda _plan, spec, *, workspace=None: {
                    "id": f"page_detail:{spec['page_id']}",
                    "page_id": spec["page_id"],
                    "page_name": spec["page_name"],
                    "path": spec["path"],
                    "status": "confirmed",
                    "source_page_spec": spec,
                    "workspace": workspace,
                },
            ) as designer:
                result = detail_confirmation(
                    {
                        "request": "我选择 页面：库存管理列表页 做详细设计",
                        "workspace": workspace,
                        "project_plan": project_plan,
                        "timeline": [],
                    }
                )

        spec = result["confirmed_page_spec"]
        self.assertEqual(designer.call_args.kwargs["workspace"], workspace)
        self.assertEqual(result["selected_page_id"], "inventory_management_list_page")
        self.assertEqual(spec["page_id"], "inventory_management_list_page")
        self.assertTrue(spec["data_source_ids"])
        self.assertTrue(spec["api_contract_ids"])
        self.assertIn("page_dependencies", spec)
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["pending_project_plan"]["page_detail_plans"][0]["page_id"],
            spec["page_id"],
        )
        self.assertEqual(
            result["clarification"]["mode"],
            "project_plan_adjustment_confirmation",
        )

    def test_agent_file_changes_are_returned_in_node_update(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))

        def design_with_file_change(
            _plan: dict,
            spec: dict,
            *,
            workspace: str | None = None,
        ) -> dict:
            assert workspace is not None
            Path(workspace, "detail-agent.txt").write_text(
                "changed by detail agent\n",
                encoding="utf-8",
            )
            return {
                "id": f"page_detail:{spec['page_id']}",
                "page_id": spec["page_id"],
                "page_name": spec["page_name"],
                "path": spec["path"],
                "status": "confirmed",
            }

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.planning.design_page_with_main_agent",
                side_effect=design_with_file_change,
            ):
                result = detail_confirmation(
                    {
                        "request": "我选择 页面：库存管理列表页 做详细设计",
                        "workspace": workspace,
                        "project_plan": project_plan,
                        "timeline": [],
                    }
                )

        self.assertEqual(
            result["code_changes"]["files"][0]["path"],
            "detail-agent.txt",
        )
        self.assertEqual(result["code_change_sets"], [result["code_changes"]])

    def test_detail_confirmation_asks_for_missing_page_spec_aspects(self) -> None:
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
        self.assertIn("页面权限", result["clarification"]["missing_aspects"])
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

        self.assertEqual(result["selected_data_source_id"], "inventory_management_source")
        self.assertEqual(result["detail_plans"][0]["type"], "data_source")
        self.assertEqual(
            result["pending_project_plan"]["data_source_detail_plans"][0]["data_source_id"],
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
