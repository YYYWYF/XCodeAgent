"""验证返回首页的轻量查询与完整 Git 快照口径一致。"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.protocols.version_control import build_version_control_ag_ui_stream, version_control_capabilities
from app.services.version_control import InspectAllVersionControlRequest, VersionControlError, inspect_all_version_control
from app.services.version_control_pending import InspectPendingCountRequest, PendingCodeCount, inspect_pending_count
from app.services.workspace_process_registry import workspace_process_registry


class PendingCountTests(unittest.TestCase):
    """覆盖真实仓库状态、命令数量、工作线程和标准 AG-UI 生命周期。"""

    def setUp(self) -> None:
        """在临时目录准备含基线提交的独立仓库。"""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.git("init")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.test")
        (self.root / "tracked.txt").write_text("baseline\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "baseline")

    def git(self, *arguments: str) -> None:
        """仅在测试仓库执行 Git 布置命令。"""
        subprocess.run(["git", *arguments], cwd=self.root, capture_output=True, check=True)

    def count(self, root: Path | None = None) -> int:
        """调用生产轻量服务并校验规范化后的返回目录。"""
        result = inspect_pending_count(InspectPendingCountRequest(action="inspect_pending_count", workspaceRoot=str(root or self.root)))
        self.assertEqual(result.workspace_root, str((root or self.root).resolve()))
        return result.pending_code_count

    def assert_matches_snapshot(self) -> None:
        """静态现场的轻量计数须与完整快照业务路径数一致。"""
        full = inspect_all_version_control(InspectAllVersionControlRequest(action="inspect_all", workspaceRoot=str(self.root)))
        self.assertEqual(self.count(), len(full.code_paths))

    def test_status_parity_and_exactly_two_commands(self) -> None:
        """干净、暂存、未暂存、未跟踪与重命名场景均保留原有计数。"""
        self.assertEqual(self.count(), 0)
        (self.root / "tracked.txt").write_text("changed\n")
        (self.root / "new.txt").write_text("new\n")
        self.assert_matches_snapshot()
        self.git("add", "new.txt")
        self.assert_matches_snapshot()
        self.git("mv", "tracked.txt", "renamed.txt")
        self.assert_matches_snapshot()
        with patch.object(workspace_process_registry, "run", wraps=workspace_process_registry.run) as run:
            self.assertEqual(self.count(), 2)
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ["git", "rev-parse", "--show-toplevel"],
            ["git", "-c", "core.quotepath=false", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        ])

    def test_platform_artifacts_are_excluded(self) -> None:
        """只有平台产物及运行时文件变化时返回零。"""
        for relative in (".devagentstudio/plans/plan.json", ".devagentstudio/runtime/log.txt"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}")
        self.assertEqual(self.count(), 0)
        self.assert_matches_snapshot()

    def test_rejects_parent_repository_and_missing_git(self) -> None:
        """子目录不能借用父仓库；没有 Git 时返回已有业务错误。"""
        child = self.root / "child"
        child.mkdir()
        with self.assertRaises(VersionControlError):
            self.count(child)
        with patch("app.services.version_control.shutil.which", return_value=None), self.assertRaises(VersionControlError):
            self.count()

    def test_ag_ui_lifecycle_and_worker_thread(self) -> None:
        """成功和 Git 异常均发出完整生命周期，实际查询执行于工作线程。"""
        main_thread = threading.get_ident()
        threads: list[int] = []

        def inspect(request: InspectPendingCountRequest) -> PendingCodeCount:
            """记录实际查询线程以防同步 Git 重新阻塞事件循环。"""
            threads.append(threading.get_ident())
            return inspect_pending_count(request)

        async def collect() -> str:
            """消费公开 AG-UI 入口至终态。"""
            return "".join([frame async for frame in build_version_control_ag_ui_stream(payload={
                "runId": "pending-run", "threadId": "pending-thread",
                "forwardedProps": {"versionControl": {"action": "inspect_pending_count", "workspaceRoot": str(self.root)}},
            })])

        for failure in (False, True):
            with self.subTest(failure=failure), patch("app.protocols.version_control.inspect_pending_count", side_effect=OSError("git failed") if failure else inspect):
                result = asyncio.run(collect())
            for event in ("RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "CUSTOM", "STATE_SNAPSHOT", "RUN_FINISHED"):
                self.assertIn(event, result)
            self.assertIn('"status":"failed"' if failure else '"pendingCodeCount":0', result)
        self.assertTrue(threads)
        self.assertTrue(all(thread != main_thread for thread in threads))
        self.assertIn("inspect_pending_count", version_control_capabilities()["actions"])
