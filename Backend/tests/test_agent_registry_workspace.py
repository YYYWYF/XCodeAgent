from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agents import registry
from app.protocols.workflow.definition import workflow_capabilities


class AgentRegistryWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        registry.clear_agent_bundle_cache()

    def tearDown(self) -> None:
        registry.clear_agent_bundle_cache()

    def test_workflow_capabilities_publish_only_registered_agents(self) -> None:
        """公开技能能力必须与当前六个 Agent 的注册表保持一致。"""

        forced_agents = workflow_capabilities()["skillSelection"]["forcedAgents"]

        self.assertEqual(
            forced_agents,
            [
                "frontend",
                "data_source",
                "database",
                "repair_planner",
                "small_task",
                "workspace_assistant",
            ],
        )

    def test_agent_bundles_are_cached_by_workspace_root(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first_workspace,
            tempfile.TemporaryDirectory() as second_workspace,
            patch(
                "app.agents.registry.get_user_skill_runtime_revision",
                return_value="skills-v1",
            ),
            patch(
                "app.agents.registry.get_agent_memory_runtime_revision",
                return_value="memory-v1",
            ),
            patch(
                "app.agents.registry.create_user_skill_runtime_snapshot",
                return_value=SimpleNamespace(backend="user-skills", issues=()),
            ),
            patch(
                "app.agents.registry.create_agent_memory_runtime_snapshot",
                return_value=SimpleNamespace(backend="agent-memory"),
            ),
            patch("app.agents.registry.Settings.from_env", return_value=object()),
            patch("app.agents.registry.create_chat_model", return_value="model"),
            patch(
                "app.agents.registry.create_code_analyze_agent",
                side_effect=lambda model, **kwargs: ("code_analyze", kwargs),
            ),
            patch(
                "app.agents.registry.create_code_review_repair_agent",
                side_effect=lambda model, **kwargs: ("code_review_repair", kwargs),
            ),
            patch(
                "app.agents.registry.create_frontend_agent",
                side_effect=lambda model, **kwargs: ("frontend", kwargs),
            ) as frontend_factory,
            patch(
                "app.agents.registry.create_data_source_agent",
                side_effect=lambda model, **kwargs: ("data_source", kwargs),
            ),
            patch(
                "app.agents.registry.create_database_agent",
                side_effect=lambda model, **kwargs: ("database", kwargs),
            ),
            patch(
                "app.agents.registry.create_repair_planner_agent",
                side_effect=lambda model, **kwargs: ("repair_planner", kwargs),
            ) as repair_planner_factory,
            patch(
                "app.agents.registry.create_small_task_agent",
                side_effect=lambda model, **kwargs: ("small_task", kwargs),
            ),
            patch(
                "app.agents.registry.create_workspace_assistant_agent",
                side_effect=lambda model, **kwargs: ("workspace_assistant", kwargs),
            ),
        ):
            first_bundle = registry.create_agent_bundle(first_workspace)
            first_bundle_again = registry.create_agent_bundle(first_workspace)
            second_bundle = registry.create_agent_bundle(second_workspace)

        self.assertIs(first_bundle, first_bundle_again)
        self.assertIsNot(first_bundle, second_bundle)
        self.assertEqual(frontend_factory.call_count, 2)
        self.assertEqual(repair_planner_factory.call_count, 2)
        self.assertEqual(
            first_bundle.frontend[1]["workspace_root"],
            str(Path(first_workspace).resolve()),
        )
        self.assertEqual(
            second_bundle.frontend[1]["workspace_root"],
            str(Path(second_workspace).resolve()),
        )
        self.assertEqual(first_bundle.frontend[1]["agent_memory_backend"], "agent-memory")
        self.assertEqual(
            first_bundle.repair_planner[1]["agent_memory_backend"],
            "agent-memory",
        )

    def test_agent_bundle_cache_changes_with_skill_or_memory_revision(self) -> None:
        with (
            tempfile.TemporaryDirectory() as workspace,
            patch(
                "app.agents.registry.get_user_skill_runtime_revision",
                return_value="skills-v1",
            ) as skill_revision,
            patch(
                "app.agents.registry.get_agent_memory_runtime_revision",
                return_value="memory-v1",
            ) as memory_revision,
            patch(
                "app.agents.registry.create_user_skill_runtime_snapshot",
                side_effect=lambda revision, **_kwargs: SimpleNamespace(
                    backend=f"backend-{revision}",
                    issues=(),
                ),
            ) as snapshot_factory,
            patch(
                "app.agents.registry.create_agent_memory_runtime_snapshot",
                side_effect=lambda revision: SimpleNamespace(
                    backend=f"memory-{revision}",
                ),
            ) as memory_snapshot_factory,
            patch("app.agents.registry.Settings.from_env", return_value=object()),
            patch("app.agents.registry.create_chat_model", return_value="model"),
            patch(
                "app.agents.registry.create_code_analyze_agent",
                side_effect=lambda model, **kwargs: ("code_analyze", kwargs),
            ),
            patch(
                "app.agents.registry.create_code_review_repair_agent",
                side_effect=lambda model, **kwargs: ("code_review_repair", kwargs),
            ),
            patch(
                "app.agents.registry.create_frontend_agent",
                side_effect=lambda model, **kwargs: ("frontend", kwargs),
            ),
            patch(
                "app.agents.registry.create_data_source_agent",
                side_effect=lambda model, **kwargs: ("data_source", kwargs),
            ),
            patch(
                "app.agents.registry.create_database_agent",
                side_effect=lambda model, **kwargs: ("database", kwargs),
            ),
            patch(
                "app.agents.registry.create_repair_planner_agent",
                side_effect=lambda model, **kwargs: (
                    "repair_planner",
                    kwargs,
                ),
            ),
            patch(
                "app.agents.registry.create_small_task_agent",
                side_effect=lambda model, **kwargs: ("small_task", kwargs),
            ),
            patch(
                "app.agents.registry.create_workspace_assistant_agent",
                side_effect=lambda model, **kwargs: ("workspace_assistant", kwargs),
            ),
        ):
            first = registry.create_agent_bundle(workspace)
            unchanged = registry.create_agent_bundle(workspace)
            memory_revision.return_value = "memory-v2"
            updated = registry.create_agent_bundle(workspace)
            skill_revision.return_value = "skills-v2"
            skills_updated = registry.create_agent_bundle(workspace)

        self.assertIs(first, unchanged)
        self.assertIsNot(first, updated)
        self.assertIsNot(updated, skills_updated)
        self.assertEqual(snapshot_factory.call_count, 3)
        self.assertEqual(memory_snapshot_factory.call_count, 3)
        self.assertEqual(
            updated.frontend[1]["user_skills_backend"],
            "backend-skills-v1",
        )
        self.assertEqual(
            updated.frontend[1]["agent_memory_backend"],
            "memory-memory-v2",
        )
        self.assertEqual(
            skills_updated.frontend[1]["user_skills_backend"],
            "backend-skills-v2",
        )

    def test_agent_bundle_cache_and_prompt_are_scoped_to_normalized_selection(self) -> None:
        prompt_document = SimpleNamespace(
            name="alpha",
            virtual_path="/.xcodeagent/user-skills/alpha/SKILL.md",
            content="complete alpha instructions",
        )
        with (
            tempfile.TemporaryDirectory() as workspace,
            patch("app.agents.registry.get_user_skill_runtime_revision", return_value="skills-v1"),
            patch("app.agents.registry.get_agent_memory_runtime_revision", return_value="memory-v1"),
            patch(
                "app.agents.registry.create_user_skill_runtime_snapshot",
                side_effect=lambda _revision, selected_skill_names=None: SimpleNamespace(
                    backend=f"skills:{selected_skill_names}",
                    issues=(),
                    prompt_documents=(prompt_document,) if selected_skill_names else (),
                ),
            ),
            patch(
                "app.agents.registry.create_agent_memory_runtime_snapshot",
                return_value=SimpleNamespace(backend="agent-memory"),
            ),
            patch("app.agents.registry.Settings.from_env", return_value=object()),
            patch("app.agents.registry.create_chat_model", return_value="model"),
            patch(
                "app.agents.registry.create_code_analyze_agent",
                side_effect=lambda model, **kwargs: ("code_analyze", kwargs),
            ),
            patch(
                "app.agents.registry.create_code_review_repair_agent",
                side_effect=lambda model, **kwargs: ("code_review_repair", kwargs),
            ),
            patch(
                "app.agents.registry.create_frontend_agent",
                side_effect=lambda model, **kwargs: ("frontend", kwargs),
            ),
            patch(
                "app.agents.registry.create_data_source_agent",
                side_effect=lambda model, **kwargs: ("data_source", kwargs),
            ),
            patch(
                "app.agents.registry.create_database_agent",
                side_effect=lambda model, **kwargs: ("database", kwargs),
            ),
            patch(
                "app.agents.registry.create_repair_planner_agent",
                side_effect=lambda model, **kwargs: ("repair", kwargs),
            ),
            patch(
                "app.agents.registry.create_small_task_agent",
                side_effect=lambda model, **kwargs: ("small_task", kwargs),
            ),
            patch(
                "app.agents.registry.create_workspace_assistant_agent",
                side_effect=lambda model, **kwargs: ("workspace_assistant", kwargs),
            ),
        ):
            first = registry.create_agent_bundle(workspace, ["beta", "alpha", "alpha"])
            reordered = registry.create_agent_bundle(workspace, ["alpha", "beta"])
            different = registry.create_agent_bundle(workspace, ["alpha"])

        self.assertIs(first, reordered)
        self.assertIsNot(first, different)
        self.assertEqual(first.selected_skill_names, ("alpha", "beta"))
        for agent in (
            first.frontend,
            first.data_source,
            first.database,
            first.repair_planner,
            first.small_task,
            first.workspace_assistant,
        ):
            self.assertIn(
                "complete alpha instructions",
                agent[1]["required_user_skills_prompt"],
            )


if __name__ == "__main__":
    unittest.main()
