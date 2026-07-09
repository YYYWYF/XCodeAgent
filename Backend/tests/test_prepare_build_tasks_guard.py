from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from app.graph.nodes.tasks import prepare_build_tasks
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec


class PrepareBuildTasksGuardTests(unittest.TestCase):
    def test_prepare_build_tasks_waits_when_project_plan_is_unconfirmed(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        project_plan["confirmation_status"] = "pending_user_confirmation"

        with patch(
            "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            side_effect=AssertionError("must not prepare tasks before confirmation"),
        ):
            result = prepare_build_tasks(
                {
                    "request": "创建一个库存管理系统",
                    "project_plan": project_plan,
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "project_plan_confirmation")
        self.assertEqual(result["phase"], "prepare_build_tasks")

    def test_prepare_build_tasks_continues_after_user_confirms_project_plan(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        project_plan["confirmation_status"] = "pending_user_confirmation"

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
                return_value={
                    "tasks": [],
                    "summary": {"total": 0},
                },
            ) as preparer:
                result = prepare_build_tasks(
                    {
                        "request": "正确，继续",
                        "workspace": workspace,
                        "project_plan": project_plan,
                        "timeline": [],
                    }
                )

        self.assertEqual(preparer.call_args.args[0]["confirmation_status"], "confirmed")
        self.assertEqual(preparer.call_args.kwargs["workspace"], workspace)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["project_plan"]["confirmation_status"], "confirmed")
        self.assertEqual(result["tasks"], [])

    def test_prepare_build_tasks_confirmation_ignores_question_text_negative_words(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建一个库存管理系统"))
        project_plan["confirmation_status"] = "pending_user_confirmation"
        continuation_message = "\n".join(
            [
                "请基于原始需求和以下用户补充确认，继续生成需求文档并推进后续 workflow。",
                "",
                "原始需求：",
                "创建一个库存管理系统",
                "",
                "用户补充确认：",
                "- 计划确认：代码生成即将开始，但当前 ProjectPlan 尚未由用户确认。请确认项目规划书是否正确。正确请回复“正确，继续”；如需调整，请说明要修改的架构、API、页面、数据源、权限或验收标准。",
                "  回答：正确，继续",
            ]
        )

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
                return_value={
                    "tasks": [],
                    "summary": {"total": 0},
                },
            ) as preparer:
                result = prepare_build_tasks(
                    {
                        "request": continuation_message,
                        "workspace": workspace,
                        "project_plan": project_plan,
                        "timeline": [],
                    }
                )

        self.assertEqual(preparer.call_args.args[0]["confirmation_status"], "confirmed")
        self.assertEqual(preparer.call_args.kwargs["workspace"], workspace)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["project_plan"]["confirmation_status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
