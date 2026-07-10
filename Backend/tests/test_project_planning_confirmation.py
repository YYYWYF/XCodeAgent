from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from app.graph.nodes.planning import project_planning
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec


class ProjectPlanningConfirmationTests(unittest.TestCase):
    def test_project_planning_waits_for_user_confirmation_after_generation(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(spec)

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.planning.plan_project_with_chat_model",
                return_value=plan,
            ) as planner:
                result = project_planning(
                    {
                        "request": "创建一个库存管理系统",
                        "workspace": workspace,
                        "requirement_spec": spec,
                        "timeline": [],
                    }
                )

        planner.assert_called_once_with(spec)
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "project_plan_confirmation")
        self.assertEqual(
            result["project_plan"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_project_planning_continues_after_user_confirms_plan(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(spec)

        with tempfile.TemporaryDirectory() as workspace:
            result = project_planning(
                {
                    "request": "正确，继续",
                    "workspace": workspace,
                    "requirement_spec": spec,
                    "project_plan": plan,
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["clarification"]["status"], "clear")
        self.assertEqual(result["project_plan"]["confirmation_status"], "confirmed")

    def test_project_planning_revision_uses_existing_plan_once(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        existing_plan = create_project_plan(spec)
        revised_plan = {
            **existing_plan,
            "frontend_pages": [existing_plan["frontend_pages"][1]],
        }

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.planning.plan_project_with_chat_model",
                return_value=revised_plan,
            ) as planner:
                result = project_planning(
                    {
                        "request": "只保留库存列表页，删除其他页面",
                        "workspace": workspace,
                        "requirement_spec": spec,
                        "project_plan": existing_plan,
                        "timeline": [],
                    }
                )

        planner.assert_called_once()
        self.assertEqual(planner.call_args.kwargs["existing_plan"], existing_plan)
        self.assertIn(
            "只保留库存列表页",
            planner.call_args.args[0]["planning_adjustment_request"],
        )
        self.assertEqual(len(result["project_plan"]["frontend_pages"]), 1)

    def test_project_plan_confirmation_ignores_question_text_negative_words(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(spec)
        continuation_message = "\n".join(
            [
                "请基于原始需求和以下用户补充确认，继续生成需求文档并推进后续 workflow。",
                "",
                "原始需求：",
                "创建一个库存管理系统",
                "",
                "用户补充确认：",
                "- 计划确认：请确认已生成的项目规划书是否正确。如果正确，请回复“正确，继续”；如果需要调整，请直接写出要修改的架构、API、页面、数据源、权限或验收标准。",
                "  回答：正确，继续",
            ]
        )

        with tempfile.TemporaryDirectory() as workspace:
            result = project_planning(
                {
                    "request": continuation_message,
                    "workspace": workspace,
                    "requirement_spec": spec,
                    "project_plan": plan,
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["project_plan"]["confirmation_status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
