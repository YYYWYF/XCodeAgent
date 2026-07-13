from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.middleware.filesystem import _check_fs_permission

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
    resolve_workspace_root,
)
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.workspace.virtual_paths import host_workspace_virtual_alias


class WorkspaceScopeTests(unittest.TestCase):
    def test_resolves_existing_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            self.assertEqual(resolve_workspace_root(workspace), Path(workspace).resolve())

    def test_invalid_workspace_root_fails(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            missing = Path(workspace) / "missing"

            with self.assertRaises(ValueError):
                resolve_workspace_root(str(missing))

    def test_workspace_backend_writes_virtual_paths_to_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            backend = create_workspace_backend(workspace)

            self.assertIsInstance(backend, FilesystemBackend)
            backend.write("/data.json", '{"sbw":123}')

            self.assertEqual(
                (Path(workspace) / "data.json").read_text(encoding="utf-8"),
                '{"sbw":123}',
            )

    def test_workspace_backend_writes_virtual_app_path_to_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            backend = create_workspace_backend(workspace)

            backend.write("/app/frontend/index.tsx", "export default null\n")

            self.assertEqual(
                (Path(workspace) / "app" / "frontend" / "index.tsx").read_text(
                    encoding="utf-8"
                ),
                "export default null\n",
            )

    def test_missing_workspace_uses_state_backend_and_denies_filesystem(self) -> None:
        backend = create_workspace_backend(None)
        permissions = create_workspace_permissions(None, mode="main")

        self.assertIsInstance(backend, StateBackend)
        self.assertEqual(_check_fs_permission(permissions, "read", "/data.json"), "deny")
        self.assertEqual(_check_fs_permission(permissions, "write", "/data.json"), "deny")

    def test_builtin_skills_are_mounted_read_only_for_enabled_agents(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            backend = create_workspace_backend(
                workspace,
                include_builtin_skills=True,
            )
            permissions = create_workspace_permissions(
                workspace,
                mode="frontend",
                include_builtin_skills=True,
            )

            self.assertIsInstance(backend, CompositeBackend)
            skill_path = (
                f"{BUILTIN_SKILLS_VIRTUAL_ROOT}"
                "react-antd-v4-codegen/SKILL.md"
            )
            result = backend.read(skill_path)
            self.assertIsNone(result.error)
            self.assertIsNotNone(result.file_data)
            self.assertIn(
                "name: react-antd-v4-codegen",
                result.file_data["content"],
            )
            self.assertEqual(_check_fs_permission(permissions, "read", skill_path), "allow")
            self.assertEqual(_check_fs_permission(permissions, "write", skill_path), "deny")
            self.assertEqual(
                _check_fs_permission(permissions, "write", "/app/frontend/index.tsx"),
                "allow",
            )

    def test_missing_workspace_can_only_read_enabled_builtin_skills(self) -> None:
        backend = create_workspace_backend(None, include_builtin_skills=True)
        permissions = create_workspace_permissions(
            None,
            mode="main",
            include_builtin_skills=True,
        )
        skill_path = (
            f"{BUILTIN_SKILLS_VIRTUAL_ROOT}react-antd-v4-codegen/SKILL.md"
        )

        self.assertIsInstance(backend, CompositeBackend)
        self.assertEqual(_check_fs_permission(permissions, "read", skill_path), "allow")
        self.assertEqual(_check_fs_permission(permissions, "write", skill_path), "deny")
        self.assertEqual(_check_fs_permission(permissions, "read", "/data.json"), "deny")

    def test_user_skills_are_mounted_read_only_without_host_path_exposure(self) -> None:
        with (
            tempfile.TemporaryDirectory() as workspace,
            tempfile.TemporaryDirectory() as skills_root,
        ):
            skill_file = Path(skills_root) / "sample" / "SKILL.md"
            skill_file.parent.mkdir()
            skill_file.write_text(
                "---\nname: sample\ndescription: Sample\n---\n",
                encoding="utf-8",
            )
            backend = create_workspace_backend(
                workspace,
                user_skills_backend=FilesystemBackend(
                    root_dir=skills_root,
                    virtual_mode=True,
                ),
            )
            permissions = create_workspace_permissions(
                workspace,
                mode="data_source",
                include_user_skills=True,
            )
            virtual_path = f"{USER_SKILLS_VIRTUAL_ROOT}sample/SKILL.md"

            result = backend.read(virtual_path)

            self.assertIsInstance(backend, CompositeBackend)
            self.assertIsNone(result.error)
            self.assertNotIn(skills_root, str(result.file_data))
            self.assertEqual(_check_fs_permission(permissions, "read", virtual_path), "allow")
            self.assertEqual(_check_fs_permission(permissions, "write", virtual_path), "deny")
            self.assertEqual(
                _check_fs_permission(permissions, "write", "/app/backend/api.py"),
                "allow",
            )
            with self.assertRaisesRegex(ValueError, "Path traversal not allowed"):
                backend.read(f"{USER_SKILLS_VIRTUAL_ROOT}../outside.txt")

    def test_sensitive_files_are_denied_before_workspace_allow(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            permissions = create_workspace_permissions(workspace, mode="main")

            self.assertEqual(_check_fs_permission(permissions, "write", "/.env"), "deny")
            self.assertEqual(
                _check_fs_permission(permissions, "write", "/nested/.env"),
                "deny",
            )
            self.assertEqual(
                _check_fs_permission(permissions, "write", "/data.json"),
                "allow",
            )

    def test_host_workspace_path_cannot_be_repeated_as_virtual_path(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            permissions = create_workspace_permissions(workspace, mode="frontend")
            host_alias = host_workspace_virtual_alias(Path(workspace))

            self.assertIsNotNone(host_alias)
            self.assertEqual(
                _check_fs_permission(
                    permissions,
                    "write",
                    f"{host_alias}/app/frontend/index.tsx",
                ),
                "deny",
            )
            self.assertEqual(
                _check_fs_permission(
                    permissions,
                    "read",
                    f"{host_alias}/app/frontend/index.tsx",
                ),
                "deny",
            )
            self.assertEqual(
                _check_fs_permission(
                    permissions,
                    "write",
                    "/app/frontend/index.tsx",
                ),
                "allow",
            )

    def test_test_agent_permissions_deny_writes(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            permissions = create_workspace_permissions(workspace, mode="test")

            self.assertEqual(_check_fs_permission(permissions, "read", "/data.json"), "allow")
            self.assertEqual(_check_fs_permission(permissions, "write", "/data.json"), "deny")


if __name__ == "__main__":
    unittest.main()
