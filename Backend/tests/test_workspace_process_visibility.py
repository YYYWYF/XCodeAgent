"""验证无终端的 Windows 安装包启动后台命令时不会创建控制台窗口。"""

from __future__ import annotations

import os
import subprocess
import unittest
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import workspace_process_registry as registry_module


class WorkspaceProcessVisibilityTests(unittest.TestCase):
    """在非 Windows 主机也核验所有受控启动入口及取消命令的 Win32 参数。"""

    def test_windows_launch_entries_hide_console_and_keep_process_group(self) -> None:
        """run、managed_process、start 都须隐藏窗口，并保留调用方已有创建标志。"""

        with TemporaryDirectory() as workspace:
            for entry in ("run", "managed_process", "start"):
                with self.subTest(entry=entry):
                    process = MagicMock()
                    process.poll.return_value = 0
                    process.communicate.return_value = (b"git version", b"")
                    process.returncode = 0
                    process.stdin = process.stdout = process.stderr = None
                    registry = registry_module.WorkspaceProcessRegistry()
                    with (
                        patch.object(registry_module, "os", SimpleNamespace(name="nt", path=os.path)),
                        patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, create=True),
                        patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
                        patch.object(subprocess, "Popen", return_value=process) as popen,
                    ):
                        argv = ["git", "status", "--porcelain=v1"]
                        options = {"creationflags": 0x4000}
                        if entry == "run":
                            registry.run(argv, workspace=workspace, capture_output=True, **options)
                        elif entry == "managed_process":
                            with registry.managed_process(argv, workspace=workspace, **options):
                                pass
                        else:
                            registry.start(workspace, argv, **options)
                            registry.release(workspace, process)
                    self.assertEqual(popen.call_args.kwargs["creationflags"], 0x08004200)
                    self.assertNotIn("start_new_session", popen.call_args.kwargs)

    def test_windows_cancellation_hides_taskkill_console(self) -> None:
        """停止后台命令时启动的 taskkill 同样不能闪出窗口。"""

        process = MagicMock()
        process.poll.return_value = None
        process.pid = 1234
        with (
            patch.object(registry_module, "os", SimpleNamespace(name="nt")),
            patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            patch.object(subprocess, "run") as run,
        ):
            registry_module._terminate_process_group(process, force=True)
        run.assert_called_once_with(
            ["taskkill", "/PID", "1234", "/T", "/F"],
            capture_output=True, timeout=2, check=False, creationflags=0x08000000,
        )

    def test_posix_launch_keeps_session_without_windows_flags(self) -> None:
        """macOS/Linux 继续使用独立 session，不接收 Windows 专属创建标志。"""

        options: dict = {}
        with patch.object(registry_module, "os", SimpleNamespace(name="posix")):
            registry_module._configure_process_group(options)
        self.assertEqual(options, {"start_new_session": True})
