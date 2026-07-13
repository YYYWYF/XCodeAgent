from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepagents.backends import FilesystemBackend

from app.agents.data_source.agent import create_data_source_agent
from app.agents.frontend.agent import create_frontend_agent
from app.agents.repair_planner.agent import create_repair_planner_agent
from app.agents.test.agent import create_test_agent
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.tools.delete_file import create_delete_file_tool


class DeleteFileToolTests(unittest.TestCase):
    def test_deletes_regular_file_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "data.json"
            target.write_text('{"sbw":123}', encoding="utf-8")

            payload = self._invoke_delete(workspace, "/data.json")

            self.assertEqual(payload["status"], "deleted")
            self.assertEqual(payload["path"], "/data.json")
            self.assertFalse(target.exists())

    def test_missing_workspace_returns_error(self) -> None:
        payload = self._invoke_delete(None, "/data.json")

        self.assertEqual(payload["status"], "error")
        self.assertIn("workspaceRoot is required", payload["error"])

    def test_path_traversal_and_home_paths_are_rejected(self) -> None:
        with (
            tempfile.TemporaryDirectory() as workspace,
            tempfile.TemporaryDirectory() as outside_workspace,
        ):
            outside = Path(outside_workspace) / "escape.txt"
            outside.write_text("keep", encoding="utf-8")

            for path in ("../escape.txt", "/../escape.txt", "~/.ssh/id_rsa", "/~/file.txt"):
                with self.subTest(path=path):
                    payload = self._invoke_delete(workspace, path)
                    self.assertEqual(payload["status"], "error")

            self.assertTrue(outside.exists())

    def test_host_workspace_path_alias_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace).resolve()
            repeated_path = f"/{root.as_posix().lstrip('/')}/data.json"

            payload = self._invoke_delete(workspace, repeated_path)

            self.assertEqual(payload["status"], "error")
            self.assertIn("do not include workspaceRoot", payload["error"])

    def test_sensitive_files_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            for filename in (".env", ".npmrc", "id_rsa"):
                with self.subTest(filename=filename):
                    target = Path(workspace) / filename
                    target.write_text("secret", encoding="utf-8")

                    payload = self._invoke_delete(workspace, f"/{filename}")

                    self.assertEqual(payload["status"], "error")
                    self.assertIn("sensitive file", payload["error"])
                    self.assertTrue(target.exists())

    def test_builtin_skill_namespace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            target = (
                Path(workspace)
                / ".xcodeagent"
                / "builtin-skills"
                / "react-antd-v4-codegen"
                / "SKILL.md"
            )
            target.parent.mkdir(parents=True)
            target.write_text("keep", encoding="utf-8")

            payload = self._invoke_delete(
                workspace,
                f"{BUILTIN_SKILLS_VIRTUAL_ROOT}react-antd-v4-codegen/SKILL.md",
            )

            self.assertEqual(payload["status"], "error")
            self.assertIn("built-in skill namespace", payload["error"])
            self.assertTrue(target.exists())

    def test_user_skill_namespace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            target = (
                Path(workspace)
                / ".xcodeagent"
                / "user-skills"
                / "sample"
                / "SKILL.md"
            )
            target.parent.mkdir(parents=True)
            target.write_text("keep", encoding="utf-8")

            payload = self._invoke_delete(
                workspace,
                f"{USER_SKILLS_VIRTUAL_ROOT}sample/SKILL.md",
            )

            self.assertEqual(payload["status"], "error")
            self.assertIn("user skill namespace", payload["error"])
            self.assertTrue(target.exists())

    def test_directories_and_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            directory = root / "folder"
            directory.mkdir()
            directory_payload = self._invoke_delete(workspace, "/folder")

            self.assertEqual(directory_payload["status"], "error")
            self.assertIn("not directories", directory_payload["error"])
            self.assertTrue(directory.exists())

            target = root / "target.txt"
            target.write_text("keep", encoding="utf-8")
            link = root / "link.txt"
            try:
                link.symlink_to(target)
            except (NotImplementedError, OSError):
                self.skipTest("Symlinks are not available in this environment.")

            link_payload = self._invoke_delete(workspace, "/link.txt")

            self.assertEqual(link_payload["status"], "error")
            self.assertIn("symlink", link_payload["error"])
            self.assertTrue(link.exists())
            self.assertTrue(target.exists())

    def test_only_writable_agents_register_delete_file_tool(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            user_skills_backend = FilesystemBackend(
                root_dir=workspace,
                virtual_mode=True,
            )
            with (
                patch(
                    "app.agents.frontend.agent.create_deep_agent",
                    side_effect=lambda **kwargs: kwargs,
                ),
                patch(
                    "app.agents.data_source.agent.create_deep_agent",
                    side_effect=lambda **kwargs: kwargs,
                ),
                patch(
                    "app.agents.test.agent.create_deep_agent",
                    side_effect=lambda **kwargs: kwargs,
                ),
                patch(
                    "app.agents.repair_planner.agent.create_deep_agent",
                    side_effect=lambda **kwargs: kwargs,
                ),
            ):
                frontend = create_frontend_agent(
                    "model",
                    workspace_root=workspace,
                    user_skills_backend=user_skills_backend,
                )
                data_source = create_data_source_agent(
                    "model",
                    workspace_root=workspace,
                    user_skills_backend=user_skills_backend,
                )
                test = create_test_agent(
                    "model",
                    workspace_root=workspace,
                    user_skills_backend=user_skills_backend,
                )
                repair_planner = create_repair_planner_agent(
                    "model",
                    workspace_root=workspace,
                    user_skills_backend=user_skills_backend,
                )

        self.assertIn("delete_file", _tool_names(frontend.get("tools", [])))
        self.assertIn("delete_file", _tool_names(data_source.get("tools", [])))
        self.assertNotIn("delete_file", _tool_names(test.get("tools", [])))
        self.assertNotIn("delete_file", _tool_names(repair_planner.get("tools", [])))
        self.assertEqual(
            frontend.get("skills"),
            [BUILTIN_SKILLS_VIRTUAL_ROOT, USER_SKILLS_VIRTUAL_ROOT],
        )
        self.assertEqual(data_source.get("skills"), [USER_SKILLS_VIRTUAL_ROOT])
        self.assertEqual(test.get("skills"), [USER_SKILLS_VIRTUAL_ROOT])
        self.assertEqual(repair_planner.get("skills"), [USER_SKILLS_VIRTUAL_ROOT])

    def _invoke_delete(self, workspace: str | None, file_path: str) -> dict:
        delete_file = create_delete_file_tool(workspace)
        payload = delete_file.invoke({"file_path": file_path})
        return json.loads(payload)


def _tool_names(tools: list) -> list[str]:
    return [str(getattr(item, "name", "")) for item in tools]


if __name__ == "__main__":
    unittest.main()
