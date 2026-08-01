from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.nodes.planning import detail_confirmation
from app.services.page_detail_plan import (
    create_endpoint_detail_plan,
    create_page_detail_plan,
    extract_endpoint_detail_context,
    extract_page_detail_context,
)
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec


def _endpoint_context_for_dependency(project_plan: dict, dependency: dict) -> dict:
    """按页面依赖反查契约并构造 EndpointDetail 上下文。"""

    endpoint_id = dependency["endpoint_id"]
    contract_id = next(
        contract["id"]
        for contract in project_plan["api_contracts"]
        if any(endpoint.get("id") == endpoint_id for endpoint in contract.get("endpoints", []))
    )
    return extract_endpoint_detail_context(project_plan, contract_id, endpoint_id)


class DetailConfirmationTests(unittest.TestCase):
    def test_model_json_overrides_endpoint_detail_fields(self) -> None:
        """EndpointDetail 应接受模型正式字段覆盖。"""

        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        page_context = extract_page_detail_context(
            project_plan,
            "inventory_management_list_page",
        )
        page_detail = create_page_detail_plan(
            project_plan,
            page_context,
        )
        dependency = page_detail["endpoint_dependencies"][0]
        endpoint_context = _endpoint_context_for_dependency(project_plan, dependency)
        endpoint_detail = create_endpoint_detail_plan(
            project_plan,
            endpoint_context,
            agent_detail_plan={"processing_logic": ["按 SKU 查询库存"]},
        )

        self.assertTrue(page_detail["api_dependencies"])
        self.assertEqual(endpoint_detail["processing_logic"], ["按 SKU 查询库存"])

    def test_page_spec_defaults_when_page_description_is_missing(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个人员管理系统")
        )
        project_plan["frontend_pages"] = [
            {
                "pageId": "people_list",
                "name": "人员列表",
                "path": "/people",
                "data_dependencies": [],
                "permissions": ["user"],
            }
        ]

        page_context = extract_page_detail_context(project_plan, "people_list")

        self.assertEqual(page_context["pageId"], "people_list")
        self.assertIn("人员列表", page_context["page_goal"])

    def test_page_detail_tolerates_api_contract_without_data_source_id(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        for contract in project_plan["api_contracts"]:
            contract.pop("data_source_id", None)

        page_context = extract_page_detail_context(
            project_plan,
            "inventory_management_list_page",
        )
        page_detail = create_page_detail_plan(project_plan, page_context)

        self.assertTrue(page_context["endpoint_contracts"])
        self.assertTrue(page_detail["api_dependencies"])

    def test_page_detail_tolerates_structured_layout_and_interactions(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        page_context = extract_page_detail_context(
            project_plan,
            "inventory_management_list_page",
        )

        page_detail = create_page_detail_plan(
            project_plan,
            page_context,
            agent_detail_plan={
                "component_structure": [
                    {
                        "area": "筛选区",
                        "purpose": "按库存状态筛选",
                        "components": ["搜索框"],
                    }
                ],
            },
        )
        self.assertEqual(page_detail["component_structure"][0]["area"], "筛选区")
        self.assertEqual(page_detail["component_structure"][0]["components"], ["搜索框"])

    def test_page_detail_only_carries_the_page_endpoint_dependencies(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        page_context = extract_page_detail_context(
            project_plan,
            "inventory_management_list_page",
        )
        page_context["references"]["endpoint_dependencies"] = [
            {
                "api_contract_id": "inventory_management_source_api",
                "endpoint_id": "inventory_management_source_api.list",
                "method": "GET",
                "url": "/api/inventory-management",
                "usage": "page_load",
                "required": True,
            }
        ]

        page_detail = create_page_detail_plan(project_plan, page_context)

        self.assertEqual(
            [item["endpoint_id"] for item in page_detail["api_dependencies"]],
            ["inventory_management_source_api.list"],
        )
        self.assertEqual(
            page_detail["endpoint_dependencies"],
            page_context["references"]["endpoint_dependencies"],
        )

    def test_page_detail_confirmation_generates_required_endpoint_details(self) -> None:
        """单页设计应先补齐缺失 EndpointDetail 并纳入同轮审核。"""

        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        with patch(
            "app.graph.nodes.planning.design_page_with_chat_model",
            side_effect=create_page_detail_plan,
        ) as page_designer, patch(
            "app.graph.nodes.planning.design_endpoint_with_chat_model",
            side_effect=lambda plan, context, _request: create_endpoint_detail_plan(
                plan,
                context,
            ),
        ) as endpoint_designer, patch(
            "app.graph.nodes.planning.prepare_endpoint_database_context",
            return_value={"status": "skipped", "message": "无需数据库上下文。"},
        ):
            selected_page = project_plan["frontend_pages"][1]
            result = detail_confirmation(
                {
                    "request": "开始页面详细设计",
                    "project_plan": project_plan,
                    "selectedPageId": selected_page["pageId"],
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "detail_review")
        self.assertEqual(
            [item["target_id"] for item in result["clarification"]["review"]["pages"]],
            [selected_page["pageId"]],
        )
        self.assertEqual(
            len(result["clarification"]["review"]["endpoints"]),
            endpoint_designer.call_count,
        )
        self.assertEqual(page_designer.call_count, 1)
        self.assertGreater(endpoint_designer.call_count, 0)
        page_contexts = [call.args[1] for call in page_designer.call_args_list]
        self.assertTrue(
            any(context["endpoint_detail_summaries"] for context in page_contexts)
        )
        self.assertEqual(
            result["pending_project_plan"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_page_detail_confirmation_reuses_existing_endpoint_detail(self) -> None:
        """页面设计应复用已设计 endpoint，并把未确认详情纳入同轮审核。"""

        project_plan = create_project_plan(create_requirement_spec("创建库存管理系统"))
        selected_page = project_plan["frontend_pages"][1]
        page_context = extract_page_detail_context(project_plan, selected_page["pageId"])
        endpoint_detail = create_endpoint_detail_plan(
            project_plan,
            _endpoint_context_for_dependency(
                project_plan,
                page_context["references"]["endpoint_dependencies"][0],
            ),
        )
        endpoint_detail["status"] = "pending_user_confirmation"
        endpoint_detail["approved"] = False
        project_plan["endpoint_detail_plans"] = [endpoint_detail]

        with patch(
            "app.graph.nodes.planning.design_page_with_chat_model",
            side_effect=create_page_detail_plan,
        ), patch(
            "app.graph.nodes.planning.design_endpoint_with_chat_model",
        ) as endpoint_designer:
            result = detail_confirmation(
                {
                    "request": "开始页面详细设计",
                    "project_plan": project_plan,
                    "selectedPageId": selected_page["pageId"],
                    "timeline": [],
                }
            )

        endpoint_designer.assert_not_called()
        self.assertEqual(len(result["clarification"]["review"]["endpoints"]), 1)

    def test_detail_review_applies_page_patch_and_confirms_once(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建库存系统"))
        page_context = extract_page_detail_context(
            project_plan,
            project_plan["frontend_pages"][0]["pageId"],
        )
        pending_plan = {
            **project_plan,
            "page_detail_plans": [create_page_detail_plan(project_plan, page_context)],
            "endpoint_detail_plans": [
                create_endpoint_detail_plan(
                    project_plan,
                    _endpoint_context_for_dependency(project_plan, dependency),
                )
                for dependency in page_context["references"]["endpoint_dependencies"]
            ],
            "confirmation_status": "pending_user_confirmation",
        }
        pageId = page_context["pageId"]

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
                            "target_id": pageId,
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
                    "pageId": "inventory_management_list_page",
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
            result["project_plan"]["page_detail_plans"][0]["pageId"],
            "inventory_management_list_page",
        )


if __name__ == "__main__":
    unittest.main()
