from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.main import page_designer, planner, requirements_analyzer


class FakeMainAgent:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def invoke(self, payload: dict):
        self.payloads.append(payload)
        return {"messages": [SimpleNamespace(content='{"requirements_overview": {"summary": "ok"}}')]}


class MainAgentBoundaryTests(unittest.TestCase):
    def test_requirements_analysis_uses_workspace_scoped_main_agent(self) -> None:
        fake_agent = FakeMainAgent()

        with patch(
            "app.agents.create_agent_bundle",
            return_value=SimpleNamespace(main=fake_agent),
        ) as bundle_factory:
            result = requirements_analyzer._invoke_live_main_agent(
                "创建一个库存管理系统",
                workspace="/tmp/workspace-a",
            )

        self.assertEqual(len(result["messages"]), 1)
        bundle_factory.assert_called_once_with("/tmp/workspace-a")
        prompt = fake_agent.payloads[0]["messages"][0]["content"]
        self.assertIn("call subagents", prompt)
        self.assertIn("create project plans", prompt)

    def test_project_planning_uses_workspace_scoped_main_agent(self) -> None:
        fake_agent = FakeMainAgent()

        with patch(
            "app.agents.create_agent_bundle",
            return_value=SimpleNamespace(main=fake_agent),
        ) as bundle_factory:
            output = planner._invoke_live_main_agent(
                {"version": "0.1.0", "app_info": {"name": "Demo"}},
                workspace="/tmp/workspace-b",
            )

        self.assertIn("requirements_overview", output)
        bundle_factory.assert_called_once_with("/tmp/workspace-b")
        prompt = fake_agent.payloads[0]["messages"][0]["content"]
        self.assertIn("do not call subagents", prompt)
        self.assertIn("do not generate or modify code", prompt)

    def test_page_design_uses_workspace_scoped_main_agent(self) -> None:
        fake_agent = FakeMainAgent()

        with patch(
            "app.agents.create_agent_bundle",
            return_value=SimpleNamespace(main=fake_agent),
        ) as bundle_factory:
            output = page_designer._invoke_live_main_agent(
                {"api_contracts": [], "page_data_dependencies": []},
                {"page_id": "dashboard", "page_goal": "Show overview"},
                workspace="/tmp/workspace-c",
            )

        self.assertIn("requirements_overview", output)
        bundle_factory.assert_called_once_with("/tmp/workspace-c")
        prompt = fake_agent.payloads[0]["messages"][0]["content"]
        self.assertIn("do not call subagents", prompt)
        self.assertIn("do not generate or modify code", prompt)


if __name__ == "__main__":
    unittest.main()
