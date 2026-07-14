from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.protocols import agent_files
from app.services import agent_file_documents


class AgentFileDocumentTests(unittest.TestCase):
    def test_environment_mapping_uses_current_xcodeagent_directory(self) -> None:
        for working_dir in (
            ".xcodeagent_dev",
            ".xcodeagent_st",
            ".xcodeagent_uat",
            ".xcodeagent",
        ):
            with self.subTest(working_dir=working_dir), patch.dict(
                os.environ,
                {"XCODEAGENT_WORKING_DIR": working_dir},
            ):
                self.assertEqual(
                    agent_file_documents.resolve_agent_files_root(),
                    Path.home() / working_dir,
                )
                self.assertEqual(agent_file_documents.agent_files_root_label(), f"~/{working_dir}")

    def test_missing_document_is_seeded_with_default_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / ".xcodeagent_dev"

            document = agent_file_documents.ensure_agents_document(root=root)

            self.assertEqual(document.name, "AGENTS.md")
            self.assertEqual(document.relative_path, "AGENTS.md")
            self.assertEqual(document.content, agent_file_documents.DEFAULT_AGENTS_CONTENT)
            self.assertEqual(document.size_bytes, len(document.content.encode("utf-8")))
            self.assertIn("+00:00", document.updated_at)
            self.assertEqual((root / "AGENTS.md").read_text(encoding="utf-8"), document.content)

    def test_existing_document_is_never_overwritten_by_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / ".xcodeagent"
            root.mkdir()
            agents_file = root / "AGENTS.md"
            agents_file.write_text("# Existing instructions\n", encoding="utf-8")

            document = agent_file_documents.ensure_agents_document(root=root)

            self.assertEqual(document.content, "# Existing instructions\n")
            self.assertEqual(agents_file.read_text(encoding="utf-8"), "# Existing instructions\n")

    def test_save_preserves_mode_and_updates_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / ".xcodeagent"
            original = agent_file_documents.ensure_agents_document(root=root)
            agents_file = root / "AGENTS.md"
            agents_file.chmod(0o640)
            content = "# Updated\n\nUse project conventions.\n"

            saved = agent_file_documents.save_agents_document(
                content,
                original.revision,
                root=root,
            )

            self.assertEqual(agents_file.read_text(encoding="utf-8"), content)
            self.assertEqual(agents_file.stat().st_mode & 0o777, 0o640)
            self.assertNotEqual(saved.revision, original.revision)
            self.assertEqual(saved.size_bytes, len(content.encode("utf-8")))
            self.assertIn("+00:00", saved.updated_at)

    def test_revision_conflict_keeps_external_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / ".xcodeagent"
            original = agent_file_documents.ensure_agents_document(root=root)
            agents_file = root / "AGENTS.md"
            agents_file.write_text("# External update\n", encoding="utf-8")

            with self.assertRaises(agent_file_documents.AgentFileRevisionConflictError):
                agent_file_documents.save_agents_document(
                    "# Local update\n",
                    original.revision,
                    root=root,
                )

            self.assertEqual(agents_file.read_text(encoding="utf-8"), "# External update\n")

    def test_invalid_utf8_and_oversized_content_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / ".xcodeagent"
            original = agent_file_documents.ensure_agents_document(root=root)
            agents_file = root / "AGENTS.md"

            with self.assertRaises(agent_file_documents.AgentFileContentError):
                agent_file_documents.save_agents_document("\ud800", original.revision, root=root)
            with self.assertRaises(agent_file_documents.AgentFileContentError):
                agent_file_documents.save_agents_document(
                    "x" * (agent_file_documents.MAX_AGENTS_CONTENT_BYTES + 1),
                    original.revision,
                    root=root,
                )

            self.assertEqual(agents_file.read_text(encoding="utf-8"), original.content)

    def test_symbolic_link_is_rejected_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / ".xcodeagent"
            root.mkdir()
            target = Path(temporary_root) / "outside.md"
            target.write_text("# Outside\n", encoding="utf-8")
            agents_file = root / "AGENTS.md"
            try:
                agents_file.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")

            with self.assertRaises(agent_file_documents.AgentFilePathError):
                agent_file_documents.read_agents_document(root=root)
            self.assertEqual(target.read_text(encoding="utf-8"), "# Outside\n")


class AgentFilesAgUiTests(unittest.TestCase):
    def test_capabilities_declare_get_and_save_actions(self) -> None:
        self.assertEqual(agent_files.agent_files_capabilities()["actions"], ["get", "save"])
        self.assertEqual(agent_files.agent_files_capabilities()["endpoint"], "/agent-files/run")

    def test_get_stream_emits_standard_lifecycle_and_document(self) -> None:
        document = agent_file_documents.AgentFileDocument(
            content="# Example\n",
            revision="a" * 64,
            size_bytes=10,
            updated_at="2026-07-14T00:00:00+00:00",
        )

        async def collect() -> list[str]:
            with patch.object(agent_files, "read_agents_document", return_value=document):
                stream = agent_files.build_agent_files_ag_ui_stream(
                    payload={
                        "threadId": "agent-files-thread",
                        "runId": "agent-files-run",
                        "forwardedProps": {"agentFiles": {"action": "get"}},
                    },
                    accept="text/event-stream",
                )
                return [frame async for frame in stream]

        payload = "\n".join(asyncio.run(collect()))
        self.assertIn("RUN_STARTED", payload)
        self.assertIn("TEXT_MESSAGE_START", payload)
        self.assertIn("agent-files", payload)
        self.assertIn("STATE_SNAPSHOT", payload)
        self.assertIn("RUN_FINISHED", payload)
        self.assertIn('"action":"get"', payload)
        self.assertIn('"relativePath":"AGENTS.md"', payload)
        self.assertIn('"sizeBytes":10', payload)

    def test_save_stream_forwards_content_and_revision(self) -> None:
        document = agent_file_documents.AgentFileDocument(
            content="# Updated\n",
            revision="b" * 64,
            size_bytes=10,
            updated_at="2026-07-14T00:00:00+00:00",
        )

        async def collect() -> list[str]:
            with patch.object(agent_files, "save_agents_document", return_value=document) as save_document:
                stream = agent_files.build_agent_files_ag_ui_stream(
                    payload={
                        "forwardedProps": {
                            "agentFiles": {
                                "action": "save",
                                "content": "# Updated\n",
                                "expectedRevision": "a" * 64,
                            }
                        }
                    },
                    accept="text/event-stream",
                )
                frames = [frame async for frame in stream]
                save_document.assert_called_once_with("# Updated\n", "a" * 64)
                return frames

        payload = "\n".join(asyncio.run(collect()))
        self.assertIn('"action":"save"', payload)
        self.assertIn('"revision":"bbbb', payload)

    def test_invalid_action_returns_structured_failure(self) -> None:
        async def collect() -> list[str]:
            stream = agent_files.build_agent_files_ag_ui_stream(
                payload={"forwardedProps": {"agentFiles": {"action": "delete"}}},
                accept="text/event-stream",
            )
            return [frame async for frame in stream]

        payload = "\n".join(asyncio.run(collect()))
        self.assertIn('"status":"failed"', payload)
        self.assertIn("ValueError", payload)
        self.assertIn("RUN_FINISHED", payload)


if __name__ == "__main__":
    unittest.main()
