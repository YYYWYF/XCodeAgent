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
            self.assertIn("+new", by_path["added.txt"]["diff"])
            self.assertIn("+next", by_path["modified.txt"]["diff"])
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
