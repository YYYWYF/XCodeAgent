from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.nodes.planning import _generate_all_detail_plans, detail_confirmation


class WorkflowSelectedPageTests(unittest.TestCase):
    """验证页面选择只解析实现契约并补齐对应接口详情。"""

    def test_generates_detail_for_selected_frontend_page_only(self) -> None:
        project_plan = {
            "frontend_pages": [
                {"pageId": "dashboard", "name": "首页"},
                {"pageId": "inventory", "name": "库存页"},
            ],
            "data_sources": [],
        }
        with patch(
            "app.graph.nodes.planning.design_page_with_chat_model",
        ) as design_page:
            result = _generate_all_detail_plans(
                project_plan,
                frontend_pages=project_plan["frontend_pages"],
                selectedPageId="inventory",
            )

        design_page.assert_not_called()
        self.assertEqual(result["detail_confirmation_summary"]["total_pages"], 0)

    def test_generates_selected_page_when_formal_plan_uses_id(self) -> None:
        project_plan = {
            "frontend_pages": [
                {"id": "dashboard", "name": "首页"},
                {"id": "inventory", "name": "库存页"},
            ],
            "data_sources": [],
        }
        with patch(
            "app.graph.nodes.planning.design_page_with_chat_model",
        ) as design_page:
            result = _generate_all_detail_plans(
                project_plan,
                frontend_pages=project_plan["frontend_pages"],
                selectedPageId="inventory",
            )

        design_page.assert_not_called()
        self.assertEqual(result["detail_confirmation_summary"]["total_pages"], 0)

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

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["clarification"]["mode"], "page_implementation_ready")
        self.assertEqual(result["detail_plans"], [])

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
            "endpoint_detail_plans": [
                {
                    "api_contract_id": "weather-api",
                    "endpoint_id": "weather.detail",
                    "method": "GET",
                    "path": "/api/weather",
                    "data_origin": {
                        "source_type": "external_api",
                        "effective_source": {
                            "kind": "third_party",
                            "provider": "weather-provider",
                        },
                    },
                }
            ],
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
            enforce_entity_gate=True,
            detail_target_type="page",
        )
        self.assertEqual(result["detail_plans"][0]["endpoint_id"], "weather.detail")

    def test_acceptance_page_design_change_does_not_regenerate_page_detail(self) -> None:
        """兼容验收页面调整输入时也不得重新生成 PageDetail。"""

        current_plan = {
            "frontend_pages": [{"pageId": "inventory", "name": "库存页"}],
            "api_contracts": [],
            "data_sources": [],
        }
        with (
            patch(
                "app.graph.nodes.planning._generate_all_detail_plans",
                return_value=current_plan,
            ) as generate_details,
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
            detail_target_type="page",
            enforce_entity_gate=True,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["phase"], "detail_confirmation")
        self.assertEqual(result["clarification"]["mode"], "page_implementation_ready")


if __name__ == "__main__":
    unittest.main()
