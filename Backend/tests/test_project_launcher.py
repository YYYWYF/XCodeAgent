from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from app.graph.nodes.lifecycle import launch_project
from app.services.project_launcher import (
    _dev_server_log_is_ready,
    _preview_is_ready,
    _wait_until_ready,
    launch_frontend_project,
)


class ProjectLauncherTests(unittest.TestCase):
    def test_preview_healthcheck_accepts_http_404_as_listening(self) -> None:
        """验证 urllib 将 404 表示为 HTTPError 时仍判定服务已经监听。"""

        error = HTTPError("http://127.0.0.1:3000", 404, "Not Found", {}, None)
        with patch("app.services.project_launcher.urlopen", side_effect=error):
            ready = _preview_is_ready("http://127.0.0.1:3000")

        self.assertTrue(ready)

    def test_readiness_falls_back_to_current_launch_log_when_http_is_blocked(self) -> None:
        """验证 Python socket 受限时可通过本次 CRA 编译成功日志确认就绪。"""

        with tempfile.TemporaryDirectory() as directory:
            stdout_log = Path(directory) / "frontend.stdout.log"
            stdout_log.write_text("Starting...\nCompiled successfully!\n", encoding="utf-8")
            fake_process = SimpleNamespace(poll=lambda: None)
            with patch("app.services.project_launcher._preview_is_ready", return_value=False):
                ready = _wait_until_ready(
                    "http://127.0.0.1:3000",
                    fake_process,
                    stdout_log=stdout_log,
                )

        self.assertTrue(ready)

    def test_readiness_ignores_ready_marker_before_current_launch_offset(self) -> None:
        """验证追加日志中的历史成功记录不会让新启动被误判为就绪。"""

        with tempfile.TemporaryDirectory() as directory:
            stdout_log = Path(directory) / "frontend.stdout.log"
            historical = "Compiled successfully!\n"
            stdout_log.write_text(f"{historical}Starting...\n", encoding="utf-8")

            self.assertFalse(_dev_server_log_is_ready(stdout_log, len(historical)))

    def test_launch_frontend_project_reads_frontend_package_and_starts_dev_server(self) -> None:
        """验证 Vite 项目通过健康检查后返回可访问的预览信息。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "Frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                '{"scripts":{"dev":"vite --host 127.0.0.1"}}',
                encoding="utf-8",
            )
            (frontend / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'", encoding="utf-8")

            fake_process = SimpleNamespace(pid=12345, poll=lambda: None)
            with (
                patch("app.services.project_launcher.shutil.which", return_value="/usr/bin/pnpm"),
                patch(
                    "app.services.project_launcher.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout="installed", stderr=""),
                ) as run,
                patch(
                    "app.services.project_launcher.subprocess.Popen",
                    return_value=fake_process,
                ) as popen,
                patch("app.services.project_launcher._wait_until_ready", return_value=True),
            ):
                result = launch_frontend_project({"workspace": workspace})

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["preview_url"], "http://127.0.0.1:5173")
        self.assertTrue(result["package_json_path"].endswith("Frontend/package.json"))
        self.assertEqual(result["package_manager"], "pnpm")
        self.assertEqual(result["script"], "dev")
        self.assertEqual(result["server"]["pid"], 12345)
        self.assertEqual(run.call_args.args[0], ["pnpm", "install"])
        self.assertEqual(popen.call_args.args[0], ["pnpm", "run", "dev"])
        self.assertEqual(popen.call_args.kwargs["env"]["HOST"], "127.0.0.1")
        self.assertEqual(popen.call_args.kwargs["env"]["BROWSER"], "none")

    def test_react_scripts_launch_does_not_force_loopback_host(self) -> None:
        """验证 CRA 启动时移除可能导致 allowedHosts 校验失败的 HOST。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "Frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                '{"scripts":{"dev":"react-scripts start"}}',
                encoding="utf-8",
            )
            fake_process = SimpleNamespace(pid=12345, poll=lambda: None)
            with (
                patch.dict("app.services.project_launcher.os.environ", {"HOST": "inherited"}),
                patch("app.services.project_launcher.shutil.which", return_value="/usr/bin/npm"),
                patch(
                    "app.services.project_launcher.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout="installed", stderr=""),
                ),
                patch(
                    "app.services.project_launcher.subprocess.Popen",
                    return_value=fake_process,
                ) as popen,
                patch("app.services.project_launcher._wait_until_ready", return_value=True),
            ):
                result = launch_frontend_project({"workspace": workspace})

        self.assertEqual(result["status"], "running")
        self.assertNotIn("HOST", popen.call_args.kwargs["env"])
        self.assertEqual(popen.call_args.kwargs["env"]["BROWSER"], "none")

    def test_existing_ready_preview_is_reused_without_starting_duplicate_server(self) -> None:
        """验证调试续跑会复用已有预览，避免端口占用被误报为启动失败。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "Frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                '{"scripts":{"dev":"react-scripts start"}}',
                encoding="utf-8",
            )
            existing_server = {
                "pid": 12345,
                "preview_url": "http://127.0.0.1:3000",
                "ready": True,
                "reused": True,
            }
            with (
                patch(
                    "app.services.project_launcher._reuse_ready_server",
                    return_value=existing_server,
                ),
                patch("app.services.project_launcher._run_install") as run_install,
                patch("app.services.project_launcher._start_dev_server") as start_dev_server,
            ):
                result = launch_frontend_project({"workspace": workspace})

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["server"], existing_server)
        self.assertTrue(result["install"]["skipped"])
        run_install.assert_not_called()
        start_dev_server.assert_not_called()

    def test_exited_dev_server_is_reported_as_failed(self) -> None:
        """验证启动进程提前退出时不会仅凭 PID 误报为 running。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "Frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                '{"scripts":{"dev":"react-scripts start"}}',
                encoding="utf-8",
            )
            fake_process = SimpleNamespace(pid=12345, poll=lambda: 1)
            with (
                patch("app.services.project_launcher.shutil.which", return_value="/usr/bin/npm"),
                patch(
                    "app.services.project_launcher.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout="installed", stderr=""),
                ),
                patch(
                    "app.services.project_launcher.subprocess.Popen",
                    return_value=fake_process,
                ),
                patch("app.services.project_launcher._wait_until_ready", return_value=False),
            ):
                result = launch_frontend_project({"workspace": workspace})

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["server"]["returncode"], 1)
        self.assertIn("退出码：1", result["message"])

    def test_readiness_timeout_is_reported_as_failed(self) -> None:
        """验证进程仍存活但健康检查超时时不会进入验收阶段。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "Frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                '{"scripts":{"dev":"vite"}}',
                encoding="utf-8",
            )
            fake_process = SimpleNamespace(pid=12345, poll=lambda: None)
            with (
                patch("app.services.project_launcher.shutil.which", return_value="/usr/bin/npm"),
                patch(
                    "app.services.project_launcher.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout="installed", stderr=""),
                ),
                patch(
                    "app.services.project_launcher.subprocess.Popen",
                    return_value=fake_process,
                ),
                patch("app.services.project_launcher._wait_until_ready", return_value=False),
            ):
                result = launch_frontend_project({"workspace": workspace})

        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["server"]["returncode"])
        self.assertIn("健康检查超时", result["message"])

    def test_readiness_check_stops_when_process_has_exited(self) -> None:
        """验证健康检查在子进程退出后不会继续请求预览地址。"""

        fake_process = SimpleNamespace(poll=lambda: 1)
        with patch("app.services.project_launcher.urlopen") as urlopen:
            ready = _wait_until_ready("http://127.0.0.1:3000", fake_process)

        self.assertFalse(ready)
        urlopen.assert_not_called()

    def test_install_timeout_bytes_are_logged_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                '{"scripts":{"start":"react-scripts start"}}',
                encoding="utf-8",
            )
            timeout = subprocess.TimeoutExpired(
                cmd=["npm", "install"],
                timeout=120,
                output=b"partial output",
                stderr=b"network timeout",
            )
            with (
                patch(
                    "app.services.project_launcher.shutil.which",
                    return_value="/usr/bin/npm",
                ),
                patch(
                    "app.services.project_launcher.subprocess.run",
                    side_effect=timeout,
                ),
            ):
                result = launch_frontend_project({"workspace": workspace})

            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["install"]["timed_out"])
            self.assertEqual(
                Path(result["install"]["stdout_log"]).read_text(encoding="utf-8"),
                "partial output",
            )

    def test_launch_project_returns_acceptance_request_after_successful_launch(self) -> None:
        launch_result = {
            "status": "running",
            "message": "ok",
            "preview_url": "http://127.0.0.1:5173",
            "package_json_path": "/workspace/Frontend/package.json",
            "server": {"pid": 123},
        }
        with patch(
            "app.graph.nodes.lifecycle.launch_frontend_project",
            return_value=launch_result,
        ):
            result = launch_project({"workspace": "/workspace"})

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["preview_url"], "http://127.0.0.1:5173")
        self.assertEqual(result["acceptance_request"]["preview_url"], "http://127.0.0.1:5173")
        self.assertEqual(result["launch_result"], launch_result)

    def test_launch_project_reports_startup_failure(self) -> None:
        with patch(
            "app.graph.nodes.lifecycle.launch_frontend_project",
            return_value={"status": "failed", "message": "未找到前端 package.json。"},
        ):
            result = launch_project({"workspace": "/workspace"})

        self.assertEqual(result["status"], "failed")
        self.assertIn("项目启动失败", result["acceptance_request"]["message"])
        self.assertNotIn("preview_url", result)


if __name__ == "__main__":
    unittest.main()
