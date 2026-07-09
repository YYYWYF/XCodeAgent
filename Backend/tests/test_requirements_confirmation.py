from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from app.graph.nodes.requirements import requirements
from app.services.requirement_spec import create_requirement_spec
from app.tools.ask_user import clear_clarification


class RequirementsConfirmationTests(unittest.TestCase):
    def test_clear_requirement_waits_for_spec_confirmation(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_main_agent",
                return_value={
                    "requirement_spec": spec,
                    "clarification": clear_clarification(spec),
                },
            ) as analyzer:
                result = requirements(
                    {
                        "request": "创建一个库存管理系统",
                        "workspace": workspace,
                        "timeline": [],
                    }
                )

        analyzer.assert_called_once_with("创建一个库存管理系统", workspace=workspace)
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["mode"],
            "requirement_spec_confirmation",
        )
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_confirmed_requirement_spec_continues_to_planning(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        with tempfile.TemporaryDirectory() as workspace:
            result = requirements(
                {
                    "request": "正确，继续规划",
                    "workspace": workspace,
                    "requirement_spec": spec,
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["clarification"]["status"], "clear")
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "confirmed",
        )

    def test_confirmation_ignores_question_text_negative_words(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        continuation_message = "\n".join(
            [
                "请基于原始需求和以下用户补充确认，继续生成需求文档并推进后续 workflow。",
                "",
                "原始需求：",
                "创建一个库存管理系统",
                "",
                "用户补充确认：",
                "- 需求确认：请确认已生成的需求文档是否正确。如果正确，请回复“正确，继续规划”；如果需要修改，请直接写出要调整的应用信息、角色、功能、页面、数据源、流程或验收标准。",
                "  回答：正确，继续规划",
            ]
        )

        with tempfile.TemporaryDirectory() as workspace:
            result = requirements(
                {
                    "request": continuation_message,
                    "workspace": workspace,
                    "requirement_spec": spec,
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "confirmed",
        )


if __name__ == "__main__":
    unittest.main()
