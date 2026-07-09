from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
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
                "app.graph.nodes.planning.plan_project_with_main_agent",
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

        planner.assert_called_once_with(spec, workspace=workspace)
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

    def test_agent_file_changes_are_returned_in_node_update(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(spec)

        def plan_with_file_change(_spec: dict, *, workspace: str | None = None) -> dict:
            assert workspace is not None
            Path(workspace, "planning-agent.txt").write_text(
                "changed by planning agent\n",
                encoding="utf-8",
            )
            return plan

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.planning.plan_project_with_main_agent",
                side_effect=plan_with_file_change,
            ):
                result = project_planning(
                    {
                        "request": "创建一个库存管理系统",
                        "workspace": workspace,
                        "requirement_spec": spec,
                        "timeline": [],
                    }
                )

        self.assertEqual(
            result["code_changes"]["files"][0]["path"],
            "planning-agent.txt",
        )
        self.assertEqual(result["code_change_sets"], [result["code_changes"]])

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
