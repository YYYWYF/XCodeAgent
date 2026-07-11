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

    def test_detail_confirmation_generates_all_targets_for_one_batch_review(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        with patch(
            "app.graph.nodes.planning.design_page_with_chat_model",
            side_effect=create_page_detail_plan,
        ) as page_designer, patch(
            "app.graph.nodes.planning.design_data_source_with_chat_model",
            side_effect=lambda plan, source_id, _request: create_data_source_detail_plan(
                plan,
                source_id,
            ),
        ) as source_designer:
            result = detail_confirmation(
                {
                    "request": "开始整体详细设计",
                    "project_plan": project_plan,
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "detail_review")
        self.assertEqual(
            len(result["clarification"]["review"]["pages"]),
            len(project_plan["frontend_pages"]),
        )
        self.assertEqual(
            len(result["clarification"]["review"]["data_sources"]),
            len(project_plan["data_sources"]),
        )
        self.assertEqual(page_designer.call_count, len(project_plan["frontend_pages"]))
        self.assertEqual(source_designer.call_count, len(project_plan["data_sources"]))
        self.assertEqual(
            result["pending_project_plan"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_detail_review_applies_page_patch_and_confirms_once(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建库存系统"))
        page_spec = create_page_spec_from_project_plan(
            project_plan,
            project_plan["frontend_pages"][0]["id"],
        )
        pending_plan = {
            **project_plan,
            "page_detail_plans": [create_page_detail_plan(project_plan, page_spec)],
            "data_source_detail_plans": [],
            "confirmation_status": "pending_user_confirmation",
        }
        page_id = page_spec["page_id"]

        result = detail_confirmation(
            {
                "request": "已整体确认设计",
                "project_plan": project_plan,
                "pending_project_plan": pending_plan,
                "detail_review_submission": {
                    "review_status": "confirmed",
                    "target_changes": [
                        {
                            "target_type": "page",
                            "target_id": page_id,
                            "changes": {
                                "page_goal": "快速查看库存并支持批量导出",
                                "interactions": ["搜索", "筛选", "批量导出"],
                            },
                        }
                    ],
                },
                "timeline": [],
            }
        )

        detail = result["project_plan"]["page_detail_plans"][0]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["project_plan"]["confirmation_status"], "confirmed")
        self.assertEqual(detail["page_goal"], "快速查看库存并支持批量导出")
        self.assertEqual(detail["interactions"], ["搜索", "筛选", "批量导出"])

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
