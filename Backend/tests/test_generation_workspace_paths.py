from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.data_source.generator import _invoke_live_data_source_agent
from app.agents.frontend.generator import _invoke_live_frontend_agent


class RecordingAgent:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def invoke(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return {"messages": [SimpleNamespace(content="completed")]}


class GenerationWorkspacePathTests(unittest.TestCase):
    def test_frontend_agent_receives_scoped_bundle_without_host_path_in_prompt(
        self,
    ) -> None:
        workspace = "/Users/sbw/Downloads/test/manage"
        agent = RecordingAgent()

        with patch(
            "app.agents.create_agent_bundle",
            return_value=SimpleNamespace(frontend=agent),
        ) as create_bundle:
            result = _invoke_live_frontend_agent(
                project_plan={"app": {"name": "Manage"}},
                build_task_plan={"summary": {"frontend": 1}},
                tasks=[{"allowed_paths": ["frontend/**"]}],
                workspace=workspace,
                selected_skill_names=None,
            )

        create_bundle.assert_called_once_with(workspace, None)
        self.assertEqual(result, "completed")
        prompt = agent.payloads[0]["messages"][0]["content"]
        self.assertNotIn(workspace, prompt)
        self.assertNotIn("Workspace:\n", prompt)
        self.assertIn("frontend", prompt)
        self.assertNotIn("app/frontend/** means /app/frontend/**", prompt)
        self.assertIn("Never include, repeat, or reconstruct", prompt)

    def test_data_source_agent_receives_scoped_bundle_without_host_path_in_prompt(
        self,
    ) -> None:
        workspace = "C:\\Users\\sbw\\Downloads\\test\\manage"
        agent = RecordingAgent()

        with patch(
            "app.agents.create_agent_bundle",
            return_value=SimpleNamespace(data_source=agent),
        ) as create_bundle:
            result = _invoke_live_data_source_agent(
                project_plan={"app": {"name": "Manage"}},
                build_task_plan={"summary": {"data_source": 1}},
                tasks=[{"allowed_paths": ["app/backend/**"]}],
                workspace=workspace,
                selected_skill_names=None,
            )

        create_bundle.assert_called_once_with(workspace, None)
        self.assertEqual(result, "completed")
        prompt = agent.payloads[0]["messages"][0]["content"]
        self.assertNotIn(workspace, prompt)
        self.assertNotIn("Workspace:\n", prompt)
        self.assertIn('"app/backend/**"', prompt)
        self.assertNotIn("app/backend/** means /app/backend/**", prompt)
        self.assertNotIn("Never include, repeat, or reconstruct", prompt)


if __name__ == "__main__":
    unittest.main()
