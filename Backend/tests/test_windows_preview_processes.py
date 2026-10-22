"""验证 Windows 预览进程只读探测与 PID 文件整树停机。"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services import backend_process_registry, frontend_project_launcher, project_launcher
from app.utils.process_liveness import _windows_pid_is_running, pid_is_running


class WindowsPreviewProcessTests(unittest.TestCase):
    """覆盖 Win32 查询状态、资源释放及两类启动器调用。"""

    def test_windows_query_is_read_only_and_closes_64_bit_handle(self) -> None:
        """存活与已退出句柄均只读查询，且不截断句柄或发送零信号。"""

        for status, expected in ((258, True), (0, False)):
            with self.subTest(status=status):
                kernel32 = MagicMock()
                handle = 0x123456789
                kernel32.OpenProcess.return_value = handle
                kernel32.WaitForSingleObject.return_value = status
                with (
                    patch.object(ctypes, "WinDLL", return_value=kernel32, create=True),
                    patch("app.utils.process_liveness.os.name", "nt"),
                    patch("app.utils.process_liveness.os.kill") as kill,
                ):
                    self.assertEqual(pid_is_running(1234), expected)
                kill.assert_not_called()
                kernel32.OpenProcess.assert_called_once_with(0x00100000, False, 1234)
                kernel32.WaitForSingleObject.assert_called_once_with(handle, 0)
                kernel32.CloseHandle.assert_called_once_with(handle)
                self.assertIs(kernel32.OpenProcess.restype, ctypes.c_void_p)

    def test_missing_and_inaccessible_processes(self) -> None:
        """不存在返回退出，无权限保持存活，不对空句柄调用等待或关闭。"""

        for error, expected in ((87, False), (5, True)):
            with self.subTest(error=error):
                kernel32 = MagicMock()
                kernel32.OpenProcess.return_value = None
                with (
                    patch.object(ctypes, "WinDLL", return_value=kernel32, create=True),
                    patch.object(ctypes, "get_last_error", return_value=error, create=True),
                ):
                    self.assertEqual(_windows_pid_is_running(1234), expected)
                kernel32.WaitForSingleObject.assert_not_called()
                kernel32.CloseHandle.assert_not_called()

    def test_query_failure_preserves_error_and_closes_handle(self) -> None:
        """未知查询错误不能伪装为退出，并保证失败后句柄仍释放。"""

        kernel32 = MagicMock()
        kernel32.OpenProcess.return_value = 123
        kernel32.WaitForSingleObject.return_value = 0xFFFFFFFF
        with (
            patch.object(ctypes, "WinDLL", return_value=kernel32, create=True),
            patch.object(ctypes, "get_last_error", return_value=6, create=True),
            patch.object(ctypes, "WinError", return_value=OSError("query failed"), create=True),
        ):
            with self.assertRaisesRegex(OSError, "query failed"):
                _windows_pid_is_running(1234)
        kernel32.CloseHandle.assert_called_once_with(123)

    def test_launchers_share_read_only_probe(self) -> None:
        """前后端停机与预览检查都复用同一个跨平台探测。"""

        for module in (frontend_project_launcher, backend_process_registry):
            with patch.object(module, "pid_is_running", return_value=True) as probe:
                self.assertTrue(module._pid_is_running(1234))
                probe.assert_called_once_with(1234)
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "frontend.pid"
            pid_file.write_text("1234", encoding="utf-8")
            with patch.object(project_launcher, "pid_is_running", return_value=True) as probe:
                self.assertTrue(project_launcher._pid_file_is_running(pid_file))
                probe.assert_called_once_with(1234)

    def test_windows_pid_file_stops_tree_before_clearing_pid(self) -> None:
        """Backend 重启后仅有 PID 文件时，使用 taskkill 整树终止并确认退出。"""

        for exited in (True, False):
            with self.subTest(exited=exited), tempfile.TemporaryDirectory() as directory:
                pid_file = Path(directory) / "frontend.pid"
                pid_file.write_text("1234", encoding="utf-8")
                with (
                    patch.object(frontend_project_launcher.os, "name", "nt"),
                    patch.object(frontend_project_launcher, "_pid_is_running", return_value=True),
                    patch.object(frontend_project_launcher.subprocess, "run") as run,
                    patch.object(frontend_project_launcher, "_process_tree_pids") as posix_tree,
                    patch.object(frontend_project_launcher, "_wait_for_pid_exit", return_value=exited),
                ):
                    run.return_value.returncode = 0
                    result = frontend_project_launcher._stop_frontend_process_from_pid_file(pid_file)
                self.assertEqual(result["success"], exited)
                self.assertEqual(pid_file.exists(), not exited)
                run.assert_called_once_with(
                    ["taskkill", "/PID", "1234", "/T", "/F"],
                    capture_output=True,
                    timeout=frontend_project_launcher.FRONTEND_STOP_TIMEOUT_SECONDS,
                    check=False,
                )
                posix_tree.assert_not_called()

    def test_windows_taskkill_failure_keeps_pid_file(self) -> None:
        """整树终止命令失败且进程仍存活时，保留 PID 以供后续重试。"""

        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "frontend.pid"
            pid_file.write_text("1234", encoding="utf-8")
            with (
                patch.object(frontend_project_launcher.os, "name", "nt"),
                patch.object(frontend_project_launcher, "_pid_is_running", return_value=True),
                patch.object(frontend_project_launcher.subprocess, "run") as run,
            ):
                run.return_value.returncode = 1
                run.return_value.stderr = b"Access denied"
                result = frontend_project_launcher._stop_frontend_process_from_pid_file(pid_file)
            self.assertFalse(result["success"])
            self.assertIn("Access denied", result["error"])
            self.assertTrue(pid_file.exists())

    def test_native_process_liveness(self) -> None:
        """在当前操作系统以真实子进程验证存活、退出及无效 PID。"""

        self.assertFalse(pid_is_running(0))
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(pid_is_running(process.pid))
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=5)
        self.assertFalse(pid_is_running(process.pid))


if __name__ == "__main__":
    unittest.main()
