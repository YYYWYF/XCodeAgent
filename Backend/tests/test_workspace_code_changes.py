from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.workspace.code_changes import (
    build_code_change_set,
    capture_workspace_changes,
    diff_workspace_snapshots,
    snapshot_workspace,
)


class WorkspaceCodeChangeTests(unittest.TestCase):
    def test_snapshot_diff_captures_added_modified_and_deleted_files(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            (root / "modified.txt").write_text("old\n", encoding="utf-8")
            (root / "deleted.txt").write_text("gone\n", encoding="utf-8")
            before = snapshot_workspace(workspace)

            (root / "added.txt").write_text("new\n", encoding="utf-8")
            (root / "modified.txt").write_text("old\nnext\n", encoding="utf-8")
            (root / "deleted.txt").unlink()
            after = snapshot_workspace(workspace)

            files = diff_workspace_snapshots(
                before,
                after,
                source_tool="test.agent",
            )
            by_path = {item["path"]: item for item in files}

            self.assertEqual(by_path["added.txt"]["changeType"], "added")
            self.assertEqual(by_path["modified.txt"]["changeType"], "modified")
            self.assertEqual(by_path["deleted.txt"]["changeType"], "deleted")
            for item in by_path.values():
                self.assertIn("@@", item["diff"])
                self.assertIn("---", item["diff"])
                self.assertIn("+++", item["diff"])
            self.assertIn("+new", by_path["added.txt"]["diff"])
            self.assertIn("+next\n", by_path["modified.txt"]["diff"])
            self.assertIn("-gone", by_path["deleted.txt"]["diff"])
            self.assertEqual(by_path["added.txt"]["additions"], 1)
            self.assertEqual(by_path["deleted.txt"]["deletions"], 1)

    def test_build_code_change_set_summarizes_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            files = [
                {"id": "1", "path": "a.txt", "additions": 2, "deletions": 0},
                {"id": "2", "path": "a.txt", "additions": 1, "deletions": 1},
                {"id": "3", "path": "b.txt", "additions": 0, "deletions": 3},
            ]

            code_change_set = build_code_change_set(
                workspace_root=workspace,
                files=files,
                source_tool="test.agent",
            )

            self.assertIsNotNone(code_change_set)
            assert code_change_set is not None
            self.assertEqual(code_change_set["status"], "applied")
            self.assertEqual(code_change_set["workspaceName"], Path(workspace).name)
            self.assertEqual(code_change_set["summary"]["files"], 2)
            self.assertEqual(code_change_set["summary"]["additions"], 3)
            self.assertEqual(code_change_set["summary"]["deletions"], 4)

    def test_ignored_dirs_and_sensitive_files_are_not_included(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            before = snapshot_workspace(workspace)

            (root / ".env").write_text("SECRET_TOKEN=secret\n", encoding="utf-8")
            ignored_dir = root / "node_modules" / "pkg"
            ignored_dir.mkdir(parents=True)
            (ignored_dir / "index.js").write_text("secret\n", encoding="utf-8")
            after = snapshot_workspace(workspace)

            files = diff_workspace_snapshots(
                before,
                after,
                source_tool="test.agent",
            )

            self.assertEqual(files, [])

    def test_agent_internal_directory_is_not_included(self) -> None:
        """验证工作目录中的 .xcodeagent 状态文件不会进入用户代码变更集。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            before = snapshot_workspace(workspace)

            internal_dir = root / ".xcodeagent" / "cache"
            internal_dir.mkdir(parents=True)
            (internal_dir / "workspace.json").write_text("{}\n", encoding="utf-8")
            (root / "visible.py").write_text("print('visible')\n", encoding="utf-8")
            after = snapshot_workspace(workspace)

            files = diff_workspace_snapshots(before, after, source_tool="test.agent")

            self.assertEqual([item["path"] for item in files], ["visible.py"])
            assert after is not None
            self.assertNotIn(".xcodeagent/cache/workspace.json", after.files)

    def test_macos_metadata_files_are_not_included(self) -> None:
        """验证任意目录中的 macOS 元数据文件不会进入用户代码变更集。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            before = snapshot_workspace(workspace)

            (root / ".DS_Store").write_bytes(b"root metadata")
            nested_dir = root / "Frontend" / "src"
            nested_dir.mkdir(parents=True)
            (nested_dir / ".DS_Store").write_bytes(b"nested metadata")
            (nested_dir / "main.ts").write_text("export {}\n", encoding="utf-8")
            after = snapshot_workspace(workspace)

            files = diff_workspace_snapshots(before, after, source_tool="test.agent")

            self.assertEqual([item["path"] for item in files], ["Frontend/src/main.ts"])
            assert after is not None
            self.assertNotIn(".DS_Store", after.files)
            self.assertNotIn("Frontend/src/.DS_Store", after.files)

    def test_nested_paths_are_workspace_relative_posix_paths(self) -> None:
        """验证 Python 端始终返回从工作目录开始的完整 POSIX 相对路径。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            before = snapshot_workspace(workspace)
            nested_file = root / "Frontend" / "src" / "components" / "Panel.tsx"
            nested_file.parent.mkdir(parents=True)
            nested_file.write_text("export default {}\n", encoding="utf-8")
            after = snapshot_workspace(workspace)

            files = diff_workspace_snapshots(before, after, source_tool="test.agent")

            self.assertEqual(files[0]["path"], "Frontend/src/components/Panel.tsx")
            self.assertFalse(Path(files[0]["path"]).is_absolute())

    def test_binary_file_change_does_not_expose_text_diff(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            before = snapshot_workspace(workspace)

            (root / "asset.bin").write_bytes(b"\x00\x01\x02")
            after = snapshot_workspace(workspace)

            files = diff_workspace_snapshots(
                before,
                after,
                source_tool="test.agent",
            )

            self.assertEqual(len(files), 1)
            self.assertTrue(files[0]["binary"])
            self.assertEqual(files[0]["diff"], "")

    def test_capture_workspace_changes_handles_no_workspace_and_no_changes(self) -> None:
        without_workspace = capture_workspace_changes(
            workspace=None,
            source_tool="test.agent",
            action=lambda: "done",
        )
        self.assertEqual(without_workspace.value, "done")
        self.assertIsNone(without_workspace.code_change_set)

        with tempfile.TemporaryDirectory() as workspace:
            no_changes = capture_workspace_changes(
                workspace=workspace,
                source_tool="test.agent",
                action=lambda: "done",
            )

        self.assertEqual(no_changes.value, "done")
        self.assertIsNone(no_changes.code_change_set)


if __name__ == "__main__":
    unittest.main()
