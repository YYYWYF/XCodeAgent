from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents import registry


class AgentRegistryWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        registry.clear_agent_bundle_cache()

    def tearDown(self) -> None:
        registry.clear_agent_bundle_cache()

    def test_agent_bundles_are_cached_by_workspace_root(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first_workspace,
            tempfile.TemporaryDirectory() as second_workspace,
            patch("app.agents.registry.Settings.from_env", return_value=object()),
            patch("app.agents.registry.create_chat_model", return_value="model"),
            patch(
                "app.agents.registry.create_frontend_agent",
                side_effect=lambda model, *, workspace_root=None: (
                    "frontend",
                    workspace_root,
                ),
            ) as frontend_factory,
            patch(
                "app.agents.registry.create_data_source_agent",
                side_effect=lambda model, *, workspace_root=None: (
                    "data_source",
                    workspace_root,
                ),
            ),
            patch(
                "app.agents.registry.create_test_agent",
                side_effect=lambda model, *, workspace_root=None: ("test", workspace_root),
            ),
            patch(
                "app.agents.registry.create_main_agent",
                side_effect=lambda model, frontend, data_source, test, *, workspace_root=None: (
                    "main",
                    workspace_root,
                    frontend,
                    data_source,
                    test,
                ),
            ) as main_factory,
        ):
            first_bundle = registry.create_agent_bundle(first_workspace)
            first_bundle_again = registry.create_agent_bundle(first_workspace)
            second_bundle = registry.create_agent_bundle(second_workspace)

        self.assertIs(first_bundle, first_bundle_again)
        self.assertIsNot(first_bundle, second_bundle)
        self.assertEqual(frontend_factory.call_count, 2)
        self.assertEqual(main_factory.call_count, 2)
        self.assertEqual(first_bundle.frontend[1], str(Path(first_workspace).resolve()))
        self.assertEqual(second_bundle.frontend[1], str(Path(second_workspace).resolve()))
        self.assertEqual(first_bundle.main[1], str(Path(first_workspace).resolve()))
        self.assertEqual(second_bundle.main[1], str(Path(second_workspace).resolve()))


if __name__ == "__main__":
    unittest.main()
