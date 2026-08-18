from __future__ import annotations

import stat
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from deepagents.middleware.memory import MemoryMiddleware

from app.agents.data_source import agent as data_source_agent
from app.agents.frontend import agent as frontend_agent
from app.agents.repair_planner import agent as repair_planner_agent
from app.agents.workspace_scope import create_workspace_backend
from app.services import agent_memory_runtime
from app.services.agent_file_documents import (
    AgentFilePathError,
    DEFAULT_AGENTS_CONTENT,
)


class AgentMemoryRuntimeTests(unittest.TestCase):
    def test_default_document_snapshot_is_loaded_by_memory_middleware(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary_root,
            tempfile.TemporaryDirectory() as workspace,
        ):
            root = Path(temporary_root) / ".xcodeagent"
            snapshot = agent_memory_runtime.create_agent_memory_runtime_snapshot(root=root)
            backend = create_workspace_backend(
                workspace,
                agent_memory_backend=snapshot.backend,
            )
            middleware = MemoryMiddleware(
                backend=backend,
                sources=[agent_memory_runtime.AGENT_MEMORY_VIRTUAL_PATH],
            )

            update = middleware.before_agent({}, object(), {})

            self.assertIsNotNone(update)
            self.assertEqual(
                update["memory_contents"][agent_memory_runtime.AGENT_MEMORY_VIRTUAL_PATH],
                DEFAULT_AGENTS_CONTENT,
            )

    def test_snapshot_is_read_only_and_stable_after_source_changes(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary_root,
            tempfile.TemporaryDirectory() as workspace,
        ):
            root = Path(temporary_root) / ".xcodeagent"
            source = root / "AGENTS.md"
            root.mkdir()
            source.write_text("# Original\n", encoding="utf-8")
            snapshot = agent_memory_runtime.create_agent_memory_runtime_snapshot(root=root)
            source.write_text("# Changed\n", encoding="utf-8")
            backend = create_workspace_backend(
                workspace,
                agent_memory_backend=snapshot.backend,
            )

            read_result = backend.read(agent_memory_runtime.AGENT_MEMORY_VIRTUAL_PATH)
            write_result = backend.write(
                agent_memory_runtime.AGENT_MEMORY_VIRTUAL_PATH,
                "# Rejected\n",
            )
            snapshot_mode = stat.S_IMODE(
                (Path(snapshot.backend.cwd) / "AGENTS.md").stat().st_mode
            )

            self.assertIsNone(read_result.error)
            self.assertEqual(read_result.file_data["content"], "# Original\n")
            self.assertIsNotNone(write_result.error)
            self.assertEqual(snapshot_mode & 0o222, 0)

    def test_stale_revision_and_source_changes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / ".xcodeagent"
            original_revision = agent_memory_runtime.get_agent_memory_runtime_revision(root)
            (root / "AGENTS.md").write_text("# Changed\n", encoding="utf-8")

            with self.assertRaises(agent_memory_runtime.AgentMemorySnapshotChangedError):
                agent_memory_runtime.create_agent_memory_runtime_snapshot(
                    original_revision,
                    root,
                )

    def test_source_change_during_snapshot_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / ".xcodeagent"
            agent_memory_runtime.get_agent_memory_runtime_revision(root)
            original_revision = agent_memory_runtime.get_agent_memory_runtime_revision

            def change_before_final_check(*, root: Path | None = None) -> str:
                assert root is not None
                (root / "AGENTS.md").write_text("# Changed\n", encoding="utf-8")
                return original_revision(root)

            with (
                patch.object(
                    agent_memory_runtime,
                    "get_agent_memory_runtime_revision",
                    side_effect=change_before_final_check,
                ),
                self.assertRaises(agent_memory_runtime.AgentMemorySnapshotChangedError),
            ):
                agent_memory_runtime.create_agent_memory_runtime_snapshot(root=root)

    def test_symbolic_link_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            parent = Path(temporary_root)
            root = parent / ".xcodeagent"
            root.mkdir()
            target = parent / "outside.md"
            target.write_text("# Outside\n", encoding="utf-8")
            try:
                (root / "AGENTS.md").symlink_to(target)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")

            with self.assertRaises(AgentFilePathError):
                agent_memory_runtime.create_agent_memory_runtime_snapshot(root=root)

    def test_all_agent_factories_pass_memory_path_and_read_only_mount(self) -> None:
        factories = (
            (frontend_agent, frontend_agent.create_frontend_agent),
            (data_source_agent, data_source_agent.create_data_source_agent),
            (repair_planner_agent, repair_planner_agent.create_repair_planner_agent),
        )
        for module, factory in factories:
            with self.subTest(agent=factory.__name__), ExitStack() as stack:
                create_deep_agent = stack.enter_context(
                    patch.object(module, "create_deep_agent", return_value=object())
                )
                create_backend = stack.enter_context(
                    patch.object(module, "create_workspace_backend", return_value="backend")
                )
                create_permissions = stack.enter_context(
                    patch.object(module, "create_workspace_permissions", return_value=[])
                )
                if hasattr(module, "create_delete_file_tool"):
                    stack.enter_context(
                        patch.object(module, "create_delete_file_tool", return_value=object())
                    )

                factory(
                    "model",
                    user_skills_backend="user-skills",
                    agent_memory_backend="agent-memory",
                    required_user_skills_prompt="required selected skill body",
                )

                self.assertEqual(
                    create_deep_agent.call_args.kwargs["memory"],
                    [agent_memory_runtime.AGENT_MEMORY_VIRTUAL_PATH],
                )
                self.assertEqual(
                    create_backend.call_args.kwargs["agent_memory_backend"],
                    "agent-memory",
                )
                self.assertIn(
                    "required selected skill body",
                    create_deep_agent.call_args.kwargs["system_prompt"],
                )
                self.assertTrue(
                    create_permissions.call_args.kwargs["include_agent_memory"]
                )


if __name__ == "__main__":
    unittest.main()
