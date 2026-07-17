from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.protocols import code_changes
from app.main import health
from app.services.code_change_revert import (
    CodeChangeRevertError,
    CodeChangeRevertRequest,
    CodeChangeRevertResult,
    _normalize_change_path,
    revert_code_change_set,
)
from app.workspace.code_changes import (
    WorkspaceSnapshot,
    build_code_change_set,
    diff_workspace_snapshots,
    snapshot_workspace,
)


class CodeChangeRevertTests(unittest.TestCase):
    def test_windows_separators_are_normalized_to_workspace_paths(self) -> None:
        """验证历史 Windows 路径会转换为统一的工作区 POSIX 路径。"""

        self.assertEqual(
            _normalize_change_path("Frontend\\src\\Panel.tsx"),
            "Frontend/src/Panel.tsx",
        )

    def test_revert_restores_modified_added_and_deleted_files(self) -> None:
        """验证一次反向补丁可同时还原修改、新增和删除文件。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            modified = root / "src" / "modified.txt"
            deleted = root / "src" / "deleted.txt"
            modified.parent.mkdir()
            modified.write_text("old\n", encoding="utf-8")
            deleted.write_text("deleted\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "initial")
            before = snapshot_workspace(str(root))

            modified.write_text("old\nnew\n", encoding="utf-8")
            deleted.unlink()
            (root / "src" / "added.txt").write_text("added\n", encoding="utf-8")
            change_set = self._change_set(root, before)

            result = revert_code_change_set(self._request(root, change_set))

            self.assertEqual(modified.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(deleted.read_text(encoding="utf-8"), "deleted\n")
            self.assertFalse((root / "src" / "added.txt").exists())
            self.assertEqual(
                result.reverted_paths,
                ["src/added.txt", "src/deleted.txt", "src/modified.txt"],
            )

    def test_revert_applies_multiple_changes_for_one_file_in_reverse_order(self) -> None:
        """验证同一文件的连续多段变更会按产生顺序倒序撤销。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            target = root / "example.txt"
            target.write_text("one\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "initial")

            before_first = snapshot_workspace(str(root))
            target.write_text("one\ntwo\n", encoding="utf-8")
            first_files = self._changed_files(root, before_first)
            before_second = snapshot_workspace(str(root))
            target.write_text("one\ntwo\nthree\n", encoding="utf-8")
            second_files = self._changed_files(root, before_second)
            change_set = build_code_change_set(
                workspace_root=root,
                files=[*first_files, *second_files],
                source_tool="test.agent",
            )
            assert change_set is not None

            revert_code_change_set(self._request(root, change_set))

            self.assertEqual(target.read_text(encoding="utf-8"), "one\n")

    def test_revert_preserves_later_non_conflicting_changes(self) -> None:
        """验证后续不冲突的用户编辑会在精确撤销后保留。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            target = root / "example.txt"
            target.write_text("one\nbase\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "initial")
            before = snapshot_workspace(str(root))
            target.write_text("one\nagent\nbase\n", encoding="utf-8")
            change_set = self._change_set(root, before)
            target.write_text("one\nagent\nbase\nlater\n", encoding="utf-8")

            revert_code_change_set(self._request(root, change_set))

            self.assertEqual(target.read_text(encoding="utf-8"), "one\nbase\nlater\n")

    def test_workspace_nested_inside_repository_uses_repository_relative_paths(self) -> None:
        """验证工作目录位于仓库子目录时仍只撤销该工作区文件。"""

        with tempfile.TemporaryDirectory() as repository:
            git_root = self._init_repository(Path(repository))
            workspace_root = git_root / "apps" / "demo"
            workspace_root.mkdir(parents=True)
            target = workspace_root / "src" / "example.txt"
            target.parent.mkdir()
            target.write_text("old\n", encoding="utf-8")
            self._git(git_root, "add", ".")
            self._git(git_root, "commit", "-m", "initial")
            before = snapshot_workspace(str(workspace_root))
            target.write_text("new\n", encoding="utf-8")
            change_set = self._change_set(workspace_root, before)

            revert_code_change_set(self._request(workspace_root, change_set))

            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")

    def test_non_git_workspace_returns_clear_error(self) -> None:
        """验证非 Git 工作目录会在修改文件前返回明确错误。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            target = root / "plain.txt"
            before = snapshot_workspace(str(root))
            target.write_text("new\n", encoding="utf-8")
            change_set = self._change_set(root, before)

            with self.assertRaisesRegex(CodeChangeRevertError, "不是 Git 工程"):
                revert_code_change_set(self._request(root, change_set))

            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")

    def test_conflicting_later_edit_keeps_workspace_unchanged(self) -> None:
        """验证补丁冲突时不会产生部分撤销。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            target = root / "example.txt"
            other = root / "other.txt"
            target.write_text("old\n", encoding="utf-8")
            other.write_text("old other\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "initial")
            before = snapshot_workspace(str(root))
            target.write_text("agent\n", encoding="utf-8")
            other.write_text("agent other\n", encoding="utf-8")
            change_set = self._change_set(root, before)
            target.write_text("later\n", encoding="utf-8")

            with self.assertRaisesRegex(CodeChangeRevertError, "无法安全撤销"):
                revert_code_change_set(self._request(root, change_set))

            self.assertEqual(target.read_text(encoding="utf-8"), "later\n")
            self.assertEqual(other.read_text(encoding="utf-8"), "agent other\n")

    def test_staged_target_file_is_rejected(self) -> None:
        """验证目标文件已暂存时不会修改工作区或索引。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            target = root / "example.txt"
            target.write_text("old\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "initial")
            before = snapshot_workspace(str(root))
            target.write_text("new\n", encoding="utf-8")
            change_set = self._change_set(root, before)
            self._git(root, "add", "example.txt")

            with self.assertRaisesRegex(CodeChangeRevertError, "已暂存"):
                revert_code_change_set(self._request(root, change_set))

            self.assertEqual(target.read_text(encoding="utf-8"), "new\n")

    def test_binary_truncated_and_unsafe_paths_are_rejected(self) -> None:
        """验证缺少完整文本补丁或路径不安全时拒绝撤销。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            base_file = {
                "path": "example.txt",
                "changeType": "modified",
                "diff": "--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-old\n+new\n",
            }
            for override, expected in (
                ({"binary": True}, "二进制"),
                ({"truncated": True}, "已截断"),
                ({"path": "../outside.txt"}, "路径无效"),
            ):
                with self.subTest(override=override):
                    change_set = {
                        "id": "changes:test",
                        "workspaceRoot": str(root),
                        "files": [{**base_file, **override}],
                    }
                    with self.assertRaisesRegex(CodeChangeRevertError, expected):
                        revert_code_change_set(self._request(root, change_set))

    def _init_repository(self, root: Path) -> Path:
        """创建供单元测试使用的最小 Git 仓库。"""

        self._git(root, "init")
        self._git(root, "config", "user.email", "tests@example.com")
        self._git(root, "config", "user.name", "Tests")
        return root

    def _git(self, root: Path, *args: str) -> str:
        """运行测试限定的 Git 命令并返回标准输出。"""

        completed = subprocess.run(
            ["git", *args],
            cwd=str(root),
            text=True,
            capture_output=True,
            check=True,
        )
        return completed.stdout

    def _changed_files(self, root: Path, before: WorkspaceSnapshot) -> list[dict[str, object]]:
        """基于指定快照读取当前工作区的文件变化。"""

        return diff_workspace_snapshots(
            before,
            snapshot_workspace(str(root)),
            source_tool="test.agent",
        )

    def _change_set(self, root: Path, before: WorkspaceSnapshot) -> dict[str, object]:
        """构建单元测试使用的完整代码变更集。"""

        change_set = build_code_change_set(
            workspace_root=root,
            files=self._changed_files(root, before),
            source_tool="test.agent",
        )
        assert change_set is not None
        return change_set

    def _request(
        self,
        root: Path,
        change_set: dict[str, object],
    ) -> CodeChangeRevertRequest:
        """构建已由用户确认的撤销请求。"""

        return CodeChangeRevertRequest.model_validate(
            {
                "action": "revert",
                "confirmed": True,
                "workspaceRoot": str(root),
                "changeSet": change_set,
            }
        )


class CodeChangesAgUiTests(unittest.TestCase):
    def test_capabilities_and_stream_expose_revert_result(self) -> None:
        """验证代码撤销能力和 AG-UI 生命周期使用稳定契约。"""

        self.assertEqual(code_changes.code_changes_capabilities()["actions"], ["revert"])
        self.assertEqual(
            code_changes.code_changes_capabilities()["endpoint"],
            "/code-changes/run",
        )

        async def collect() -> list[str]:
            """收集测试中的异步 AG-UI 帧。"""

            result = {
                "action": "revert",
                "changeSetId": "changes:test",
                "workspaceRoot": "/workspace",
                "revertedPaths": ["src/example.ts"],
                "revertedAt": "2026-07-17T00:00:00+00:00",
            }
            with patch.object(
                code_changes,
                "revert_code_change_set",
                return_value=CodeChangeRevertResult.model_validate(result),
            ):
                stream = code_changes.build_code_changes_ag_ui_stream(
                    payload={
                        "threadId": "code-changes-thread",
                        "runId": "code-changes-run",
                        "forwardedProps": {
                            "codeChangesAction": {
                                "action": "revert",
                                "confirmed": True,
                                "workspaceRoot": "/workspace",
                                "changeSet": {
                                    "id": "changes:test",
                                    "workspaceRoot": "/workspace",
                                    "files": [
                                        {
                                            "path": "src/example.ts",
                                            "changeType": "modified",
                                            "diff": "patch",
                                        }
                                    ],
                                },
                            }
                        },
                    },
                    accept="text/event-stream",
                )
                return [frame async for frame in stream]

        payload = "\n".join(asyncio.run(collect()))
        self.assertIn("RUN_STARTED", payload)
        self.assertIn("TEXT_MESSAGE_START", payload)
        self.assertIn("code-changes", payload)
        self.assertIn("STATE_SNAPSHOT", payload)
        self.assertIn("RUN_FINISHED", payload)
        self.assertIn('"changeSetId":"changes:test"', payload)
        self.assertIn('"revertedPaths":["src/example.ts"]', payload)

    def test_health_declares_code_changes_capabilities(self) -> None:
        """验证健康检查公开代码变更 AG-UI 能力。"""

        payload = asyncio.run(health())

        self.assertEqual(
            payload["tools"]["code_changes"]["endpoint"],  # type: ignore[index]
            "/code-changes/run",
        )


if __name__ == "__main__":
    unittest.main()
