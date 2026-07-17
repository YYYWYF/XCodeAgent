from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.nodes.planning import _generate_all_detail_plans


class WorkflowSelectedPageTests(unittest.TestCase):
    """验证页面选择只启动对应页面的细节设计。"""

    def test_generates_detail_for_selected_frontend_page_only(self) -> None:
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
                return_value={"page_id": "inventory"},
            ) as extract_context,
            patch(
                "app.graph.nodes.planning.design_page_with_chat_model",
                return_value={"page_id": "inventory"},
            ) as design_page,
            patch(
                "app.graph.nodes.planning.attach_page_detail_plan",
                side_effect=lambda plan, detail: plan,
            ),
        ):
            result = _generate_all_detail_plans(
                project_plan,
                frontend_pages=project_plan["frontend_pages"],
                selected_page_id="inventory",
            )

        extract_context.assert_called_once_with(project_plan, "inventory")
        design_page.assert_called_once()
        self.assertEqual(result["detail_confirmation_summary"]["total_pages"], 1)


if __name__ == "__main__":
    unittest.main()
