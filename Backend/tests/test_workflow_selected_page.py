from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.nodes.planning import _generate_all_detail_plans, detail_confirmation


class WorkflowSelectedPageTests(unittest.TestCase):
    """验证页面选择只启动对应页面的细节设计。"""

    def test_generates_detail_for_selected_frontend_page_only(self) -> None:
        project_plan = {
            "frontend_pages": [
                {"pageId": "dashboard", "name": "首页"},
                {"pageId": "inventory", "name": "库存页"},
            ],
            "data_sources": [],
        }
        with (
            patch(
                "app.graph.nodes.planning.extract_page_detail_context",
                return_value={"pageId": "inventory"},
            ) as extract_context,
            patch(
                "app.graph.nodes.planning.design_page_with_chat_model",
                return_value={"pageId": "inventory"},
            ) as design_page,
            patch(
                "app.graph.nodes.planning.attach_page_detail_plan",
                side_effect=lambda plan, detail: plan,
            ),
        ):
            result = _generate_all_detail_plans(
                project_plan,
                frontend_pages=project_plan["frontend_pages"],
                selectedPageId="inventory",
            )

        self.assertEqual(extract_context.call_args.args[1], "inventory")
        design_page.assert_called_once()
        self.assertEqual(result["detail_confirmation_summary"]["total_pages"], 1)

    def test_generates_selected_page_when_formal_plan_uses_id(self) -> None:
        project_plan = {
            "frontend_pages": [
                {"id": "dashboard", "name": "首页"},
                {"id": "inventory", "name": "库存页"},
            ],
            "data_sources": [],
        }
        with (
            patch(
                "app.graph.nodes.planning.extract_page_detail_context",
                return_value={"pageId": "inventory"},
            ) as extract_context,
            patch(
                "app.graph.nodes.planning.design_page_with_chat_model",
                return_value={"pageId": "inventory"},
            ) as design_page,
            patch(
                "app.graph.nodes.planning.attach_page_detail_plan",
                side_effect=lambda plan, detail: plan,
            ),
        ):
            result = _generate_all_detail_plans(
                project_plan,
                frontend_pages=project_plan["frontend_pages"],
                selectedPageId="inventory",
            )

        normalized_plan = extract_context.call_args.args[0]
        self.assertEqual(
            [page["pageId"] for page in normalized_plan["frontend_pages"]],
            ["dashboard", "inventory"],
        )
        extract_context.assert_called_once_with(normalized_plan, "inventory")
        design_page.assert_called_once()
        self.assertEqual(result["detail_confirmation_summary"]["total_pages"], 1)

    def test_detail_confirmation_reviews_generated_formal_id_page(self) -> None:
        project_plan = {
            "frontend_pages": [
                {
                    "id": "inventory",
                    "name": "库存页",
                    "path": "/inventory",
                    "references": {
                        "permissions": [],
                        "endpoint_dependencies": [],
                        "navigation_targets": [],
                    },
                }
            ],
            "api_contracts": [],
            "data_sources": [],
        }
        with (
            patch(
                "app.graph.nodes.planning.extract_page_detail_context",
                return_value={"pageId": "inventory"},
            ),
            patch(
                "app.graph.nodes.planning.design_page_with_chat_model",
                return_value={
                    "id": "page_detail:inventory",
                    "pageId": "inventory",
                    "page_name": "库存页",
                    "path": "/inventory",
                },
            ),
        ):
            result = detail_confirmation(
                {
                    "request": "开始设计页面：库存页",
                    "project_plan": project_plan,
                    "frontend_pages": project_plan["frontend_pages"],
                    "selectedPageId": "inventory",
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["review"]["summary"]["selectedPageId"],
            "inventory",
        )
        self.assertFalse(
            result["clarification"]["review"]["summary"]["missingSelectedPagePlan"]
        )
        self.assertEqual(
            result["clarification"]["review"]["pages"][0]["target_id"],
            "inventory",
        )

    def test_stale_pending_plan_generates_newly_selected_page(self) -> None:
        """旧会话待确认计划缺少当前页时，应从最新正式计划补生成而不是返回空审核。"""

        current_plan = {
            "frontend_pages": [{"pageId": "weather-detail", "name": "天气详情"}],
            "api_contracts": [],
            "data_sources": [],
        }
        stale_pending_plan = {
            **current_plan,
            "page_detail_plans": [{"pageId": "home", "page_name": "首页"}],
        }
        generated_plan = {
            **current_plan,
            "page_detail_plans": [
                {
                    "id": "page_detail:weather-detail",
                    "pageId": "weather-detail",
                    "page_name": "天气详情",
                    "path": "/weather-detail",
                    "data_sources": [],
                }
            ],
            "data_source_detail_plans": [],
        }
        with (
            patch(
                "app.graph.nodes.planning._generate_all_detail_plans",
                return_value=generated_plan,
            ) as generate_details,
            patch(
                "app.graph.nodes.planning.write_project_plan_document",
                return_value="/workspace/.xcodeagent/plans/project-plan.md",
            ),
        ):
            result = detail_confirmation(
                {
                    "request": "开始设计页面：天气详情",
                    "project_plan": current_plan,
                    "pending_project_plan": stale_pending_plan,
                    "frontend_pages": current_plan["frontend_pages"],
                    "selectedPageId": "weather-detail",
                }
            )

        generate_details.assert_called_once_with(
            current_plan,
            frontend_pages=current_plan["frontend_pages"],
            selectedPageId="weather-detail",
        )
        self.assertFalse(
            result["clarification"]["review"]["summary"]["missingSelectedPagePlan"]
        )
        self.assertEqual(result["detail_plans"][0]["pageId"], "weather-detail")

    def test_acceptance_page_design_change_generates_a_new_pending_detail_version(self) -> None:
        """验收中的页面调整必须带反馈重生成详情，并再次等待确认。"""

        current_plan = {
            "frontend_pages": [{"pageId": "inventory", "name": "库存页"}],
            "api_contracts": [],
            "data_sources": [],
        }
        generated_plan = {
            **current_plan,
            "page_detail_plans": [{"pageId": "inventory", "page_name": "库存页"}],
            "endpoint_detail_plans": [],
        }
        with (
            patch(
                "app.graph.nodes.planning._generate_all_detail_plans",
                return_value=generated_plan,
            ) as generate_details,
            patch(
                "app.graph.nodes.planning.write_project_plan_document",
                return_value="/workspace/.xcodeagent/plans/project-plan.md",
            ),
        ):
            result = detail_confirmation(
                {
                    "project_plan": current_plan,
                    "frontend_pages": current_plan["frontend_pages"],
                    "selectedPageId": "inventory",
                    "detail_review_submission": {},
                    "acceptance_adjustment": {
                        "type": "page_design_change",
                        "feedback": "把筛选区改成更紧凑的横向布局。",
                    },
                }
            )

        generate_details.assert_called_once_with(
            current_plan,
            frontend_pages=current_plan["frontend_pages"],
            selectedPageId="inventory",
            selected_api_contract_id=None,
            selected_endpoint_id=None,
            detail_target_type="page",
            workspace_root=None,
            user_request="把筛选区改成更紧凑的横向布局。",
            regenerate_endpoint_details=False,
        )
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["phase"], "detail_confirmation")
        self.assertEqual(
            result["pending_project_plan"]["confirmation_status"],
            "pending_user_confirmation",
        )


if __name__ == "__main__":
    unittest.main()
