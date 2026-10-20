"""预览端口必须来自当前应用启动日志，不能误用其他应用的 HTTP 响应。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.frontend_project_launcher import (
    _dev_server_log_is_ready, _resolve_actual_preview_url, _reuse_ready_server,
    _wait_until_ready, launch_frontend_project,
)
from app.services.preview_runtime_state import begin_attempt, finish_attempt, runtime_root, runtime_snapshot


class PreviewPortBindingTests(unittest.TestCase):
    """使用文件与 HTTP 替身重现两个应用分别监听 3000、3001 的场景。"""

    def setUp(self) -> None:
        """创建独立临时工作区，不启动或停止真实预览进程。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.log = self.root / "frontend.stdout.log"
        self.process = SimpleNamespace(poll=lambda: None)

    def test_old_port_response_does_not_beat_current_address_publication(self) -> None:
        """旧项目已响应时仍等待新项目发布 3001，不能提前确认 3000。"""

        self.log.write_text("Port 3000 is in use\nVITE ready in 200 ms\n")

        def publish(_seconds):
            """模拟下一轮轮询前，新进程完成实际地址输出。"""

            self.log.write_text(self.log.read_text() + "➜ Local: http://localhost:3001/\n")

        with patch("app.services.frontend_project_launcher._preview_is_ready", return_value=True) as probe, \
             patch("app.services.frontend_project_launcher.time.monotonic", side_effect=[0, 0, 1]), \
             patch("app.services.frontend_project_launcher.time.sleep", side_effect=publish):
            ready = _wait_until_ready("http://localhost:3000", self.process, stdout_log=self.log)
        self.assertTrue(ready)
        probe.assert_called_once_with("http://localhost:3001")

    def test_previous_log_and_vite_banner_alone_cannot_confirm_new_launch(self) -> None:
        """上轮地址和本轮单独的 Vite 横幅均不能放行，也不探测旧端口。"""

        old = "Local: http://localhost:3000/\n"
        self.log.write_text(old + "VITE ready in 200 ms\n")
        self.assertFalse(_dev_server_log_is_ready(self.log, len(old)))
        with patch("app.services.frontend_project_launcher._preview_is_ready", return_value=True) as probe, \
             patch("app.services.frontend_project_launcher.time.monotonic", side_effect=[0, 0, 30]), \
             patch("app.services.frontend_project_launcher.time.sleep"):
            self.assertFalse(_wait_until_ready("http://localhost:3000", self.process, stdout_log=self.log, stdout_offset=len(old)))
        probe.assert_not_called()

    def test_address_resolution_uses_latest_published_address(self) -> None:
        """复用日志有多次地址输出时，取最新地址而不是第一次监听地址。"""

        self.log.write_text("Local: http://localhost:3000/\nLocal: http://localhost:3001/\n")
        self.assertEqual(_resolve_actual_preview_url(self.log, 0, "http://localhost:80"), "http://localhost:3001")

    def test_reuse_probes_owned_process_actual_address(self) -> None:
        """复用受管进程时检查实际地址，死亡 PID 不能借旧项目端口复活。"""

        (self.root / "frontend.pid").write_text("12345")
        self.log.write_text("Local: http://localhost:3001/\n")
        with patch("app.services.frontend_project_launcher._pid_is_running", return_value=True), \
             patch("app.services.frontend_project_launcher._preview_is_ready", return_value=True) as probe:
            result = _reuse_ready_server(self.root, "http://localhost:3000")
        self.assertEqual(result["preview_url"], "http://localhost:3001")
        probe.assert_called_once_with("http://localhost:3001")
        with patch("app.services.frontend_project_launcher._pid_is_running", return_value=False), \
             patch("app.services.frontend_project_launcher._preview_is_ready") as probe:
            self.assertIsNone(_reuse_ready_server(self.root, "http://localhost:3000"))
        probe.assert_not_called()

    def test_launcher_returns_reused_actual_address(self) -> None:
        """外层启动结果也使用复用进程地址，不重新返回脚本默认端口。"""

        frontend = self.root / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text('{"scripts":{"dev":"vite --port 3000"}}')
        with patch("app.services.frontend_project_launcher._reuse_ready_server", return_value={"preview_url": "http://localhost:3001", "pid": 12345}):
            result = launch_frontend_project(self.root)
        self.assertEqual(result["preview_url"], "http://localhost:3001")

    def test_runtime_snapshot_corrects_current_process_port(self) -> None:
        """状态读取按存活 PID 的日志校准地址与端口，预览可自动离开旧项目。"""

        begin_attempt(self.root)
        runtime = runtime_root(self.root)
        (runtime / "frontend.pid").write_text("12345")
        (runtime / "frontend.stdout.log").write_text("Local: http://localhost:3001/\n")
        finish_attempt(self.root, {"status": "running", "preview_url": "http://localhost:3000",
            "frontend": {"status": "running", "preview_url": "http://localhost:3000",
                         "server": {"pid": 12345, "port": 3000, "ready": True}}, "backend": {"status": "skipped"}})
        with patch("app.services.preview_runtime_state.os.kill"), \
             patch("app.services.frontend_project_launcher._preview_is_ready", return_value=True) as probe:
            value = runtime_snapshot(self.root, logs=False)
        self.assertEqual(value["status"], "running")
        self.assertEqual(value["previewUrl"], "http://localhost:3001")
        self.assertEqual(value["frontend"]["url"], "http://localhost:3001")
        self.assertEqual(value["frontend"]["port"], 3001)
        probe.assert_called_once_with("http://localhost:3001")
