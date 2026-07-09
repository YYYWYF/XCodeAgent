from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.main import page_designer, planner, requirements_analyzer


class FakeModel:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        return SimpleNamespace(content='{"requirements_overview": {"summary": "ok"}}')

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self


class MainAgentBoundaryTests(unittest.TestCase):
    def test_requirements_analysis_uses_ask_user_only_model_boundary(self) -> None:
        fake_model = FakeModel()

        with patch("app.agents.main.requirements_analyzer.Settings.from_env", return_value=object()):
            with patch(
                "app.agents.main.requirements_analyzer.create_chat_model",
                return_value=fake_model,
            ):
                with patch(
                    "app.agents.create_agent_bundle",
                    side_effect=AssertionError("requirements must not construct subagent bundle"),
                ):
                    result = requirements_analyzer._invoke_live_main_agent(
                        "创建一个库存管理系统"
                    )

        self.assertEqual(len(result["messages"]), 1)
        self.assertIn("ask_user", getattr(fake_model, "bound_tools")[0].name)
        self.assertIn("call subagents", fake_model.prompts[0])
        self.assertIn("create project plans", fake_model.prompts[0])

    def test_project_planning_uses_tool_free_model_boundary(self) -> None:
        fake_model = FakeModel()

        with patch("app.agents.main.planner.Settings.from_env", return_value=object()):
            with patch("app.agents.main.planner.create_chat_model", return_value=fake_model):
                with patch(
                    "app.agents.create_agent_bundle",
                    side_effect=AssertionError("planning must not construct subagent bundle"),
                ):
                    output = planner._invoke_live_main_agent(
                        {"version": "0.1.0", "app_info": {"name": "Demo"}}
                    )

        self.assertIn("requirements_overview", output)
        self.assertIn("do not call subagents", fake_model.prompts[0])
        self.assertIn("do not generate or modify code", fake_model.prompts[0])

    def test_page_design_uses_tool_free_model_boundary(self) -> None:
        fake_model = FakeModel()

        with patch("app.agents.main.page_designer.Settings.from_env", return_value=object()):
            with patch("app.agents.main.page_designer.create_chat_model", return_value=fake_model):
                with patch(
                    "app.agents.create_agent_bundle",
                    side_effect=AssertionError("page design must not construct subagent bundle"),
                ):
                    output = page_designer._invoke_live_main_agent(
                        {"api_contracts": [], "page_data_dependencies": []},
                        {"page_id": "dashboard", "page_goal": "Show overview"},
                    )

        self.assertIn("requirements_overview", output)
        self.assertIn("do not call subagents", fake_model.prompts[0])
        self.assertIn("do not generate or modify code", fake_model.prompts[0])


if __name__ == "__main__":
    unittest.main()
