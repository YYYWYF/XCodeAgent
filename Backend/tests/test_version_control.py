"""二次修改版本控制服务与 AG-UI 协议测试。"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.protocols.version_control import (
    build_version_control_ag_ui_stream,
    version_control_capabilities,
)
from app.services.version_control import (
    CommitVersionControlRequest,
    InspectVersionControlRequest,
    VersionControlError,
    commit_version_control,
    inspect_version_control,
)


class VersionControlTests(unittest.TestCase):
    """验证提交前复核、并发保护和精确文件提交。"""

    def test_inspect_filters_current_status_to_requested_paths(self) -> None:
        """验证只返回本轮变更集仍然存在的实际 Git 修改。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            (root / "untracked.txt").write_text("new\n", encoding="utf-8")

            snapshot = inspect_version_control(
                InspectVersionControlRequest(
                    action="inspect",
                    workspaceRoot=str(root),
                    requestedPaths=["tracked.txt", "already-clean.txt"],
                )
            )

            self.assertEqual(snapshot.eligible_paths, ["tracked.txt"])
            self.assertEqual(snapshot.unavailable_paths, ["already-clean.txt"])
            self.assertTrue(snapshot.dirty)
            self.assertFalse(snapshot.has_staged_changes)

    def test_commit_only_selected_files_and_preserves_other_changes(self) -> None:
        """验证显式提交只包含用户选择文件并保留其他未提交内容。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            (root / "other.txt").write_text("other change\n", encoding="utf-8")
            snapshot = self._inspect(root, ["tracked.txt", "other.txt"])

            result = commit_version_control(
                CommitVersionControlRequest(
                    action="commit",
                    confirmed=True,
                    workspaceRoot=str(root),
                    requestedPaths=["tracked.txt", "other.txt"],
                    selectedPaths=["tracked.txt"],
                    expectedFingerprint=snapshot.fingerprint,
                    message="fix: 完成二次修改",
                )
            )

            self.assertEqual(result.committed_paths, ["tracked.txt"])
            self.assertTrue(result.remaining_dirty)
            self.assertEqual(self._git(root, "show", "--format=", "--name-only").strip(), "tracked.txt")
            self.assertIn("other.txt", self._git(root, "status", "--short"))

    def test_commit_rejects_stale_fingerprint(self) -> None:
        """验证审阅后又发生变化时必须重新生成状态快照。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            target = root / "tracked.txt"
            target.write_text("changed\n", encoding="utf-8")
            snapshot = self._inspect(root, ["tracked.txt"])
            target.write_text("changed again\n", encoding="utf-8")

            with self.assertRaisesRegex(VersionControlError, "重新审阅"):
                commit_version_control(
                    CommitVersionControlRequest(
                        action="commit",
                        confirmed=True,
                        workspaceRoot=str(root),
                        requestedPaths=["tracked.txt"],
                        selectedPaths=["tracked.txt"],
                        expectedFingerprint=snapshot.fingerprint,
                        message="fix: 完成二次修改",
                    )
                )

    def test_commit_rejects_preexisting_staged_changes(self) -> None:
        """验证已有暂存内容不会被静默合并进自动建议提交。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            self._git(root, "add", "tracked.txt")
            snapshot = self._inspect(root, ["tracked.txt"])

            with self.assertRaisesRegex(VersionControlError, "已有暂存"):
                commit_version_control(
                    CommitVersionControlRequest(
                        action="commit",
                        confirmed=True,
                        workspaceRoot=str(root),
                        requestedPaths=["tracked.txt"],
                        selectedPaths=["tracked.txt"],
                        expectedFingerprint=snapshot.fingerprint,
                        message="fix: 完成二次修改",
                    )
                )

    def test_parent_repository_is_rejected(self) -> None:
        """验证子工作区不会误用上级目录中的 Git 仓库。"""

        with tempfile.TemporaryDirectory() as repository:
            root = self._init_repository(Path(repository))
            nested = root / "apps" / "demo"
            nested.mkdir(parents=True)
            (nested / "demo.txt").write_text("demo\n", encoding="utf-8")

            with self.assertRaisesRegex(VersionControlError, "父目录仓库"):
                self._inspect(nested, ["demo.txt"])

    def test_protocol_emits_capabilities_and_completed_payload(self) -> None:
        """验证独立动作公开 AG-UI 元数据并返回标准完成态。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = self._init_repository(Path(workspace))
            (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
            payload = {
                "threadId": "version-control-thread",
                "runId": "version-control-run",
                "forwardedProps": {
                    "versionControl": {
                        "action": "inspect",
                        "workspaceRoot": str(root),
                        "requestedPaths": ["tracked.txt"],
                    }
                },
            }

            chunks = asyncio.run(self._collect_stream(payload))

            self.assertEqual(version_control_capabilities()["endpoint"], "/version-control/run")
            self.assertIn("version-control", "".join(chunks))
            self.assertIn('"status":"completed"', "".join(chunks))

    async def _collect_stream(self, payload: dict[str, object]) -> list[str]:
        """收集异步 AG-UI 事件流以供协议断言。"""

        stream = build_version_control_ag_ui_stream(payload=payload)
        return [chunk async for chunk in stream]

    def _init_repository(self, root: Path) -> Path:
        """创建具有基线提交的最小独立 Git 仓库。"""

        self._git(root, "init")
        self._git(root, "config", "user.email", "tests@example.com")
        self._git(root, "config", "user.name", "Tests")
        (root / "tracked.txt").write_text("base\n", encoding="utf-8")
        (root / "other.txt").write_text("other base\n", encoding="utf-8")
        self._git(root, "add", ".")
        self._git(root, "commit", "-m", "initial")
        return root

    def _inspect(self, root: Path, paths: list[str]):
        """用测试默认参数读取版本控制状态。"""

        return inspect_version_control(
            InspectVersionControlRequest(
                action="inspect",
                workspaceRoot=str(root),
                requestedPaths=paths,
            )
        )

    def _git(self, root: Path, *arguments: str) -> str:
        """运行测试限定的 Git 命令并返回标准输出。"""

        completed = subprocess.run(
            ["git", *arguments],
            cwd=str(root),
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if completed.returncode != 0:
            self.fail(completed.stderr or completed.stdout)
        return completed.stdout


if __name__ == "__main__":
    unittest.main()
