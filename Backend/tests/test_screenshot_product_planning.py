from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app.agents.screenshot_requirements.product_planning import (
    ScreenshotProductPlanOperationCoverageError,
    _page_candidate,
    plan_screenshot_product_with_chat_model,
)
from app.graph.nodes.product_planning import product_planning
from app.services.product_plan import validate_product_plan
from app.services.requirement_spec import create_requirement_spec


class ScreenshotProductPlanningTests(unittest.TestCase):
    def _spec(self, count: int = 5) -> dict:
        spec = create_requirement_spec("创建业务管理应用")
        source = spec["pages"][0]
        spec["pages"] = []
        for index in range(count):
            page = deepcopy(source)
            page.update(
                pageId=f"screenshot_page_{index + 1}",
                name=f"截图页面 {index + 1}",
                path=f"/screenshot-page-{index + 1}",
                description=f"页面 {index + 1}；截图可见信息：指标、状态",
                visible_information=["指标", "状态"],
                visible_controls=["筛选"],
            )
            spec["pages"].append(page)
        spec["analysis_source"] = "screenshot_vision_model"
        return spec

    @staticmethod
    def _model_page(page: dict) -> dict:
        return {
            "pageId": page["pageId"],
            "name": page["name"],
            "path": page["path"],
            "module_id": page["module_id"],
            "description": page["description"],
            "goal": "查看页面指标与状态。",
            "information_items": [
                {"itemId": "metric", "label": "指标", "description": "展示当前指标。"}
            ],
            "actions": [
                {
                    "actionId": f"{page['pageId']}_filter",
                    "name": "筛选",
                    "description": "按条件筛选结果。",
                    "requiresConfirmation": False,
                    "behavior": {"type": "business", "expectedResult": "结果列表更新。"},
                }
            ],
            "navigation_targets": [],
            "state_requirements": {
                "loading": "展示加载状态。",
                "empty": "展示空状态。",
                "error": "展示错误状态。",
                "success": "展示成功状态。",
                "validation": "展示输入校验状态。",
            },
            "acceptance_criteria": ["可以查看指标并筛选结果。"],
        }

    def test_five_pages_are_generated_separately_and_validated_together(self) -> None:
        spec = self._spec()
        responses = [json.dumps(self._model_page(page), ensure_ascii=False) for page in spec["pages"]]
        with patch(
            "app.agents.screenshot_requirements.product_planning._invoke_page_model",
            side_effect=responses,
        ) as model:
            plan = plan_screenshot_product_with_chat_model(spec)

        self.assertEqual(model.call_count, 5)
        self.assertEqual(
            [page["pageId"] for page in plan["pages"]],
            [page["pageId"] for page in spec["pages"]],
        )
        self.assertEqual(plan["product_acceptance_criteria"], spec["acceptance_criteria"])
        self.assertEqual(validate_product_plan(plan, spec), [])
        for call, page in zip(model.call_args_list, spec["pages"], strict=True):
            self.assertIn(f'"pageId": "{page["pageId"]}"', call.args[0])
            self.assertIn("Return exactly ONE complete JSON page object", call.args[0])

    def test_truncated_page_retries_without_accepting_partial_content(self) -> None:
        spec = self._spec(1)
        page = spec["pages"][0]
        complete = json.dumps(self._model_page(page), ensure_ascii=False)
        candidate, errors = _page_candidate(spec, page, complete[:-25])
        self.assertIsNone(candidate)
        self.assertIn("截断", errors[0])
        with patch(
            "app.agents.screenshot_requirements.product_planning._invoke_page_model",
            side_effect=[complete[:-25], complete],
        ) as model:
            plan = plan_screenshot_product_with_chat_model(spec)
        self.assertEqual(model.call_count, 2)
        self.assertEqual(validate_product_plan(plan, spec), [])

    def test_missing_page_fields_retry_instead_of_persisting_partial_plan(self) -> None:
        spec = self._spec(1)
        page = spec["pages"][0]
        complete_page = self._model_page(page)
        partial_page = deepcopy(complete_page)
        partial_page.pop("acceptance_criteria")
        partial_page["state_requirements"].pop("error")
        partial_page["state_requirements"].pop("success")
        partial_page["state_requirements"].pop("validation")
        with patch(
            "app.agents.screenshot_requirements.product_planning._invoke_page_model",
            side_effect=[
                json.dumps(partial_page, ensure_ascii=False),
                json.dumps(complete_page, ensure_ascii=False),
            ],
        ) as model:
            plan = plan_screenshot_product_with_chat_model(spec)
        self.assertEqual(model.call_count, 2)
        self.assertIn("acceptance_criteria", model.call_args.args[0])
        self.assertEqual(validate_product_plan(plan, spec), [])

    def test_unmapped_restricted_operation_keeps_existing_clarification_path(self) -> None:
        spec = self._spec(1)
        spec["authorization_requirements"] = {
            "restrictedPages": [],
            "restrictedOperations": [
                {"ruleId": "delete_rule", "name": "删除记录", "description": "删除记录"}
            ],
        }
        response = json.dumps(self._model_page(spec["pages"][0]), ensure_ascii=False)
        with patch(
            "app.agents.screenshot_requirements.product_planning._invoke_page_model",
            return_value=response,
        ):
            with self.assertRaises(ScreenshotProductPlanOperationCoverageError) as caught:
                plan_screenshot_product_with_chat_model(spec)
        self.assertEqual(caught.exception.coverage[0]["name"], "删除记录")

    @patch("app.graph.nodes.product_planning.write_product_plan_documents")
    @patch("app.graph.nodes.product_planning._generate_valid_product_plan")
    @patch("app.graph.nodes.product_planning.plan_screenshot_product_with_chat_model")
    def test_screenshot_mode_uses_adapter_not_original_generation(
        self, screenshot_planner, original_planner, writer
    ) -> None:
        spec = self._spec(1)
        screenshot_planner.return_value = {"app": {"name": "截图应用"}}
        writer.return_value = ("product-plan.md", "product-plan.json")
        update = product_planning(
            {"requirement_spec": spec, "requirement_input": {"mode": "screenshot"}, "request": ""}
        )
        self.assertEqual(update["status"], "requires_user_input")
        screenshot_planner.assert_called_once()
        original_planner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
