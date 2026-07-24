from __future__ import annotations

import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch
from urllib.error import HTTPError

from app.graph.nodes.lifecycle import acceptance, launch_project
from app.services import backend_process_registry
from app.services.backend_project_launcher import (
    _backend_logs_are_ready,
    _find_backend_snapshot_jar,
    _wait_for_backend_ready,
)
from app.services.backend_process_registry import (
    backend_launch_lock,
    register_backend_process,
    stop_previous_backend_process,
)
from app.services.project_launcher import (
    _dev_server_log_is_ready,
    _preview_is_ready,
    _wait_until_ready,
    launch_backend_project,
    launch_frontend_project,
    stop_backend_project,
)


class ProjectLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        """清空跨用例共享的后端进程登记，避免临时工作区之间相互影响。"""

        backend_process_registry._BACKEND_PROCESSES.clear()
        backend_process_registry._BACKEND_LAUNCH_LOCKS.clear()

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
            package_manager_command = r"C:\Program Files\nodejs\pnpm.cmd"
            with (
                patch(
                    "app.services.project_launcher.shutil.which",
                    return_value=package_manager_command,
                ),
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
                result = launch_frontend_project(workspace)

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["preview_url"], "http://127.0.0.1:80")
        self.assertTrue(result["package_json_path"].lower().endswith("frontend/package.json"))
        self.assertEqual(result["package_manager"], "pnpm")
        self.assertEqual(result["script"], "dev")
        self.assertEqual(result["server"]["pid"], 12345)
        self.assertEqual(run.call_args.args[0], [package_manager_command, "install"])
        self.assertEqual(
            popen.call_args.args[0],
            [package_manager_command, "run", "dev"],
        )
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
                result = launch_frontend_project(workspace)

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
                result = launch_frontend_project(workspace)

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
                result = launch_frontend_project(workspace)

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
                result = launch_frontend_project(workspace)

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
        """验证前端安装超时的 bytes 输出可以安全写入日志。"""

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
                result = launch_frontend_project(workspace)

            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["install"]["timed_out"])
            self.assertEqual(
                Path(result["install"]["stdout_log"]).read_text(encoding="utf-8"),
                "partial output",
            )

    def test_frontend_install_oserror_is_returned_as_structured_failure(self) -> None:
        """验证 Windows 包管理器启动异常不会中断 Workflow。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                '{"scripts":{"start":"react-scripts start"}}',
                encoding="utf-8",
            )
            package_manager_command = r"C:\Program Files\nodejs\npm.cmd"
            error = FileNotFoundError(2, "系统找不到指定的文件", package_manager_command)
            with (
                patch(
                    "app.services.project_launcher.shutil.which",
                    return_value=package_manager_command,
                ),
                patch(
                    "app.services.project_launcher.subprocess.run",
                    side_effect=error,
                ),
            ):
                result = launch_frontend_project(workspace)

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["message"], "前端依赖安装命令执行失败。")
            self.assertEqual(result["install"]["argv"][0], package_manager_command)
            self.assertEqual(result["install"]["error"], str(error))
            self.assertEqual(
                Path(result["install"]["stderr_log"]).read_text(encoding="utf-8"),
                str(error),
            )

    def test_launch_backend_project_builds_and_starts_unique_snapshot_jar(self) -> None:
        """验证 Java 后端按 Maven 构建、JAR 启动的顺序成功运行。"""

        with tempfile.TemporaryDirectory() as workspace:
            backend = Path(workspace) / "backend"
            target = backend / "target"
            target.mkdir(parents=True)
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            jar_path = target / "testApp-1.0.0-SNAPSHOT.jar"
            jar_path.write_bytes(b"jar")
            fake_process = SimpleNamespace(pid=24680, poll=lambda: None)
            maven_command = r"C:\Program Files\Maven\bin\mvn.cmd"
            java_command = r"C:\Program Files\Java\bin\java.exe"
            with (
                patch(
                    "app.services.backend_project_launcher.shutil.which",
                    side_effect=[maven_command, java_command],
                ) as which,
                patch(
                    "app.services.backend_project_launcher.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout="built", stderr=""),
                ) as run,
                patch(
                    "app.services.backend_project_launcher.subprocess.Popen",
                    return_value=fake_process,
                ) as popen,
                patch(
                    "app.services.backend_project_launcher._wait_for_backend_ready",
                    return_value=True,
                ),
            ):
                result = launch_backend_project(workspace)

        self.assertEqual(result["status"], "running")
        self.assertEqual(Path(result["jar_path"]), jar_path.resolve())
        self.assertIs(result["_process"], fake_process)
        self.assertEqual(which.call_args_list, [call("mvn"), call("java")])
        self.assertEqual(
            run.call_args.args[0],
            [maven_command, "clean", "install"],
        )
        self.assertEqual(Path(run.call_args.kwargs["cwd"]), backend.resolve())
        self.assertEqual(
            popen.call_args.args[0],
            [java_command, "-jar", str(jar_path.resolve())],
        )
        self.assertEqual(Path(popen.call_args.kwargs["cwd"]), target.resolve())
        self.assertTrue(result["prebuild_cleanup"]["success"])
        self.assertEqual(result["prebuild_cleanup"]["source"], "none")

    def test_second_backend_launch_stops_registered_process_before_maven_build(self) -> None:
        """验证同一工作区再次启动时先停止旧 Java 进程，再执行 Maven 构建。"""

        events: list[str] = []
        with tempfile.TemporaryDirectory() as workspace:
            backend = Path(workspace) / "backend"
            target = backend / "target"
            target.mkdir(parents=True)
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            (target / "app-1.0-SNAPSHOT.jar").write_bytes(b"jar")
            first_process = MagicMock(pid=111)
            first_process.poll.side_effect = [None, None, 0]
            first_process.terminate.side_effect = lambda: events.append("terminate")
            second_process = MagicMock(pid=222)
            second_process.poll.return_value = None

            def run_build(*args: object, **kwargs: object) -> SimpleNamespace:
                """记录 Maven 调用顺序并返回成功结果。"""

                events.append("build")
                return SimpleNamespace(returncode=0, stdout="built", stderr="")

            with (
                patch(
                    "app.services.backend_project_launcher.shutil.which",
                    return_value="/usr/bin/tool",
                ),
                patch(
                    "app.services.backend_project_launcher.subprocess.run",
                    side_effect=run_build,
                ),
                patch(
                    "app.services.backend_project_launcher.subprocess.Popen",
                    side_effect=[first_process, second_process],
                ),
                patch(
                    "app.services.backend_project_launcher._wait_for_backend_ready",
                    return_value=True,
                ),
            ):
                first = launch_backend_project(workspace)
                second = launch_backend_project(workspace)

        self.assertEqual(first["status"], "running")
        self.assertEqual(second["status"], "running")
        self.assertEqual(events, ["build", "terminate", "build"])
        self.assertEqual(second["prebuild_cleanup"]["source"], "memory")
        self.assertTrue(second["prebuild_cleanup"]["terminated"])

    def test_backend_cleanup_failure_aborts_maven_build(self) -> None:
        """验证旧进程无法安全停止时返回清理失败且不调用 Maven。"""

        with tempfile.TemporaryDirectory() as workspace:
            backend = Path(workspace) / "backend"
            backend.mkdir()
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            cleanup = {
                "success": False,
                "source": "pid_file",
                "pid": 123,
                "error": "identity unknown",
            }
            with (
                patch(
                    "app.services.backend_project_launcher.shutil.which",
                    return_value="/usr/bin/tool",
                ),
                patch(
                    "app.services.backend_project_launcher.stop_previous_backend_process",
                    return_value=cleanup,
                ),
                patch(
                    "app.services.backend_project_launcher.subprocess.run"
                ) as run,
            ):
                result = launch_backend_project(workspace)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_stage"], "backend_cleanup")
        self.assertEqual(result["prebuild_cleanup"], cleanup)
        run.assert_not_called()

    def test_pid_file_recovers_and_stops_matching_backend_process(self) -> None:
        """验证服务重启后可从 PID 文件恢复并停止身份匹配的 Java 后端。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            backend = root / "backend"
            target = backend / "target"
            runtime = root / ".xcodeagent" / "runtime" / "launch"
            target.mkdir(parents=True)
            runtime.mkdir(parents=True)
            jar_path = target / "app-1.0-SNAPSHOT.jar"
            jar_path.write_bytes(b"jar")
            pid_file = runtime / "backend.pid"
            pid_file.write_text("24680", encoding="utf-8")
            with (
                patch(
                    "app.services.backend_process_registry._pid_is_running",
                    side_effect=[True, False],
                ),
                patch(
                    "app.services.backend_process_registry._query_process_command",
                    return_value=(f"java -jar {jar_path.resolve()}", None),
                ),
                patch("app.services.backend_process_registry.os.kill") as kill,
            ):
                cleanup = stop_previous_backend_process(
                    workspace=root,
                    backend_root=backend,
                    runtime_root=runtime,
                )
            pid_file_removed = not pid_file.exists()

        self.assertTrue(cleanup["success"])
        self.assertTrue(cleanup["identity_matched"])
        self.assertEqual(cleanup["source"], "pid_file")
        self.assertTrue(pid_file_removed)
        kill.assert_called_once_with(24680, backend_process_registry.signal.SIGTERM)

    def test_invalid_and_stale_pid_files_are_handled_deterministically(self) -> None:
        """验证损坏 PID 会阻止构建，而已退出 PID 会作为陈旧记录清理。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            backend = root / "backend"
            runtime = root / ".xcodeagent" / "runtime" / "launch"
            runtime.mkdir(parents=True)
            pid_file = runtime / "backend.pid"
            pid_file.write_text("not-a-pid", encoding="utf-8")
            invalid = stop_previous_backend_process(
                workspace=root,
                backend_root=backend,
                runtime_root=runtime,
            )
            pid_file.write_text("13579", encoding="utf-8")
            with patch(
                "app.services.backend_process_registry._pid_is_running",
                return_value=False,
            ):
                stale = stop_previous_backend_process(
                    workspace=root,
                    backend_root=backend,
                    runtime_root=runtime,
                )
            stale_pid_removed = not pid_file.exists()

        self.assertFalse(invalid["success"])
        self.assertIn("PID", invalid["error"])
        self.assertTrue(stale["success"])
        self.assertTrue(stale["stale"])
        self.assertTrue(stale_pid_removed)

    def test_pid_file_refuses_to_stop_unrelated_java_process(self) -> None:
        """验证 PID 指向其他 Java 命令时拒绝终止并保留诊断文件。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            backend = root / "backend"
            runtime = root / ".xcodeagent" / "runtime" / "launch"
            runtime.mkdir(parents=True)
            pid_file = runtime / "backend.pid"
            pid_file.write_text("97531", encoding="utf-8")
            with (
                patch(
                    "app.services.backend_process_registry._pid_is_running",
                    return_value=True,
                ),
                patch(
                    "app.services.backend_process_registry._query_process_command",
                    return_value=("java -jar other-project.jar", None),
                ),
                patch("app.services.backend_process_registry.os.kill") as kill,
            ):
                cleanup = stop_previous_backend_process(
                    workspace=root,
                    backend_root=backend,
                    runtime_root=runtime,
                )
            pid_file_preserved = pid_file.exists()

        self.assertFalse(cleanup["success"])
        self.assertFalse(cleanup["identity_matched"])
        self.assertTrue(pid_file_preserved)
        kill.assert_not_called()

    def test_recovered_pid_is_forced_and_reports_final_failure(self) -> None:
        """验证恢复 PID 温和停止超时后会强杀，且无法退出时保留明确错误。"""

        successful = backend_process_registry._cleanup_result(
            attempted=True,
            source="pid_file",
            pid=123,
        )
        with (
            patch("app.services.backend_process_registry.os.kill"),
            patch(
                "app.services.backend_process_registry._wait_for_pid_exit",
                side_effect=[False, True],
            ),
            patch("app.services.backend_process_registry._force_kill_pid") as force,
        ):
            backend_process_registry._terminate_recovered_pid(123, successful)

        failed = backend_process_registry._cleanup_result(
            attempted=True,
            source="pid_file",
            pid=456,
        )
        with (
            patch("app.services.backend_process_registry.os.kill"),
            patch(
                "app.services.backend_process_registry._wait_for_pid_exit",
                side_effect=[False, False],
            ),
            patch("app.services.backend_process_registry._force_kill_pid") as failed_force,
        ):
            backend_process_registry._terminate_recovered_pid(456, failed)

        self.assertTrue(successful["success"])
        self.assertTrue(successful["forced"])
        force.assert_called_once_with(123)
        self.assertFalse(failed["success"])
        self.assertTrue(failed["forced"])
        self.assertIn("仍无法确认", failed["error"])
        failed_force.assert_called_once_with(456)

    def test_backend_registry_is_workspace_scoped_and_launch_lock_serializes(self) -> None:
        """验证不同工作区登记互不影响，并且同一工作区启动锁会串行执行。"""

        with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
            first_root = Path(first_directory)
            second_root = Path(second_directory)
            process = MagicMock(pid=12345)
            register_backend_process(first_root, process)
            second_cleanup = stop_previous_backend_process(
                workspace=second_root,
                backend_root=second_root / "backend",
                runtime_root=second_root / ".xcodeagent" / "runtime" / "launch",
            )

            entered: list[str] = []
            first_acquired = threading.Event()
            release_first = threading.Event()

            def hold_lock() -> None:
                """持有工作区启动锁，直到测试允许释放。"""

                with backend_launch_lock(first_root):
                    entered.append("first")
                    first_acquired.set()
                    release_first.wait(timeout=1)

            def wait_for_lock() -> None:
                """等待同一工作区启动锁并记录进入顺序。"""

                first_acquired.wait(timeout=1)
                with backend_launch_lock(first_root):
                    entered.append("second")

            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(hold_lock)
                second_future = executor.submit(wait_for_lock)
                self.assertTrue(first_acquired.wait(timeout=1))
                time.sleep(0.02)
                self.assertEqual(entered, ["first"])
                release_first.set()
                first_future.result(timeout=1)
                second_future.result(timeout=1)

        self.assertTrue(second_cleanup["success"])
        self.assertEqual(second_cleanup["source"], "none")
        self.assertIs(
            backend_process_registry._BACKEND_PROCESSES[
                backend_process_registry._workspace_key(first_root)
            ],
            process,
        )
        self.assertEqual(entered, ["first", "second"])

    def test_launch_backend_project_requires_maven_project_and_runtime_tools(self) -> None:
        """验证 backend/pom.xml、mvn 和 java 均为启动前置条件。"""

        with tempfile.TemporaryDirectory() as workspace:
            missing_project = launch_backend_project(workspace)
            backend = Path(workspace) / "backend"
            backend.mkdir()
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            with patch(
                "app.services.backend_project_launcher.shutil.which",
                return_value=None,
            ):
                missing_maven = launch_backend_project(workspace)
            with patch(
                "app.services.backend_project_launcher.shutil.which",
                side_effect=["/usr/bin/mvn", None],
            ):
                missing_java = launch_backend_project(workspace)

        self.assertEqual(missing_project["failed_stage"], "backend_validation")
        self.assertIn("backend/pom.xml", missing_project["message"])
        self.assertIn("mvn", missing_maven["message"])
        self.assertIn("java", missing_java["message"])

    def test_launch_backend_project_reports_maven_failure_without_starting_java(self) -> None:
        """验证 Maven 构建失败时保存日志且不会启动 Java。"""

        with tempfile.TemporaryDirectory() as workspace:
            backend = Path(workspace) / "backend"
            backend.mkdir()
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            with (
                patch(
                    "app.services.backend_project_launcher.shutil.which",
                    return_value="/usr/bin/tool",
                ),
                patch(
                    "app.services.backend_project_launcher.subprocess.run",
                    return_value=SimpleNamespace(
                        returncode=1,
                        stdout="compile output",
                        stderr="compile error",
                    ),
                ),
                patch(
                    "app.services.backend_project_launcher.subprocess.Popen"
                ) as popen,
            ):
                result = launch_backend_project(workspace)

            self.assertEqual(result["failed_stage"], "backend_build")
            self.assertEqual(result["build"]["returncode"], 1)
            self.assertEqual(
                Path(result["build"]["stderr_log"]).read_text(encoding="utf-8"),
                "compile error",
            )
            popen.assert_not_called()

    def test_backend_build_oserror_is_returned_as_structured_failure(self) -> None:
        """验证 Windows mvn.cmd 启动异常会记录证据而不是冒泡终止 Workflow。"""

        with tempfile.TemporaryDirectory() as workspace:
            backend = Path(workspace) / "backend"
            backend.mkdir()
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            maven_command = r"C:\Program Files\Maven\bin\mvn.cmd"
            java_command = r"C:\Program Files\Java\bin\java.exe"
            error = FileNotFoundError(2, "系统找不到指定的文件", maven_command)
            with (
                patch(
                    "app.services.backend_project_launcher.shutil.which",
                    side_effect=[maven_command, java_command],
                ),
                patch(
                    "app.services.backend_project_launcher.subprocess.run",
                    side_effect=error,
                ),
                patch(
                    "app.services.backend_project_launcher.subprocess.Popen"
                ) as popen,
            ):
                result = launch_backend_project(workspace)

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failed_stage"], "backend_build")
            self.assertEqual(result["message"], "后端 Maven 构建命令执行失败。")
            self.assertEqual(result["build"]["argv"][0], maven_command)
            self.assertEqual(result["build"]["error"], str(error))
            self.assertEqual(
                Path(result["build"]["stderr_log"]).read_text(encoding="utf-8"),
                str(error),
            )
            popen.assert_not_called()

    def test_launch_backend_project_rejects_missing_or_ambiguous_snapshot_jar(self) -> None:
        """验证 Maven 成功后缺少唯一主 JAR 时仍然不会启动 Java。"""

        with tempfile.TemporaryDirectory() as workspace:
            backend = Path(workspace) / "backend"
            backend.mkdir()
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            with (
                patch(
                    "app.services.backend_project_launcher.shutil.which",
                    return_value="/usr/bin/tool",
                ),
                patch(
                    "app.services.backend_project_launcher.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout="built", stderr=""),
                ),
                patch(
                    "app.services.backend_project_launcher.subprocess.Popen"
                ) as popen,
            ):
                missing = launch_backend_project(workspace)
                target = backend / "target"
                target.mkdir()
                (target / "first-1.0-SNAPSHOT.jar").write_bytes(b"jar")
                (target / "second-1.0-SNAPSHOT.jar").write_bytes(b"jar")
                ambiguous = launch_backend_project(workspace)

        self.assertEqual(missing["failed_stage"], "backend_jar")
        self.assertEqual(missing["jar_candidates"], [])
        self.assertEqual(ambiguous["failed_stage"], "backend_jar")
        self.assertEqual(len(ambiguous["jar_candidates"]), 2)
        popen.assert_not_called()

    def test_snapshot_jar_selection_excludes_auxiliary_jars_and_rejects_ambiguity(self) -> None:
        """验证附属 JAR 被排除，同时多个主 JAR 不会被随意选择。"""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            primary = target / "app-1.0-SNAPSHOT.jar"
            primary.write_bytes(b"jar")
            for name in (
                "original-app-1.0-SNAPSHOT.jar",
                "app-1.0-SNAPSHOT-sources.jar",
                "app-1.0-SNAPSHOT-javadoc.jar",
                "app-1.0-SNAPSHOT-tests.jar",
            ):
                (target / name).write_bytes(b"jar")

            selected, candidates = _find_backend_snapshot_jar(target)
            self.assertEqual(selected, primary)
            self.assertEqual(candidates, [primary])

            second = target / "other-1.0-SNAPSHOT.jar"
            second.write_bytes(b"jar")
            selected, candidates = _find_backend_snapshot_jar(target)

        self.assertIsNone(selected)
        self.assertEqual(candidates, [primary, second])

    def test_backend_readiness_accepts_only_current_version_markers(self) -> None:
        """验证两种版本标志可就绪，普通 Started 日志和历史标志均无效。"""

        with tempfile.TemporaryDirectory() as directory:
            stdout_log = Path(directory) / "backend.stdout.log"
            stderr_log = Path(directory) / "backend.stderr.log"
            historical = "Spring Boot Version 3.5\n"
            stdout_log.write_text(f"{historical}Started Application\n", encoding="utf-8")
            stderr_log.write_text("", encoding="utf-8")
            self.assertFalse(
                _backend_logs_are_ready(
                    stdout_log=stdout_log,
                    stdout_offset=len(historical),
                    stderr_log=stderr_log,
                    stderr_offset=0,
                )
            )

            stdout_log.write_text("Spring Boot Version 3.5\n", encoding="utf-8")
            self.assertTrue(
                _backend_logs_are_ready(
                    stdout_log=stdout_log,
                    stdout_offset=0,
                    stderr_log=stderr_log,
                    stderr_offset=0,
                )
            )

            stdout_log.write_text("", encoding="utf-8")
            stderr_log.write_text("ZA21 Version 2.0\n", encoding="utf-8")
            self.assertTrue(
                _backend_logs_are_ready(
                    stdout_log=stdout_log,
                    stdout_offset=0,
                    stderr_log=stderr_log,
                    stderr_offset=0,
                )
            )

    def test_backend_readiness_stops_for_exited_process_and_times_out(self) -> None:
        """验证 Java 进程提前退出或未产生版本标志时不会误报就绪。"""

        fake_process = SimpleNamespace(poll=lambda: 1)
        with tempfile.TemporaryDirectory() as directory:
            stdout_log = Path(directory) / "stdout.log"
            stderr_log = Path(directory) / "stderr.log"
            stdout_log.write_text("Spring Boot Version 3.5\n", encoding="utf-8")
            stderr_log.write_text("", encoding="utf-8")
            exited = _wait_for_backend_ready(
                fake_process,
                stdout_log=stdout_log,
                stdout_offset=0,
                stderr_log=stderr_log,
                stderr_offset=0,
            )
            with patch(
                "app.services.backend_project_launcher.time.monotonic",
                side_effect=[0, 61],
            ):
                timed_out = _wait_for_backend_ready(
                    SimpleNamespace(poll=lambda: None),
                    stdout_log=stdout_log,
                    stdout_offset=len("Spring Boot Version 3.5\n"),
                    stderr_log=stderr_log,
                    stderr_offset=0,
                )

        self.assertFalse(exited)
        self.assertFalse(timed_out)

    def test_stop_backend_project_terminates_process_and_removes_pid_file(self) -> None:
        """验证前端失败回滚会终止本次 Java 进程并清理 PID 文件。"""

        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "backend.pid"
            pid_file.write_text("12345", encoding="utf-8")
            process = MagicMock(pid=12345)
            process.poll.side_effect = [None, 0, 0]
            launch_result = {"status": "running", "server": {"pid_file": str(pid_file)}}

            cleanup = stop_backend_project(launch_result, process)

        self.assertTrue(cleanup["terminated"])
        self.assertFalse(cleanup["forced"])
        self.assertEqual(launch_result["status"], "stopped")
        self.assertFalse(pid_file.exists())
        process.terminate.assert_called_once_with()

    def test_process_cleanup_preserves_newer_pid_file(self) -> None:
        """验证旧进程回滚不会删除并发启动后写入的新进程 PID。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_file = root / "backend.pid"
            pid_file.write_text("222", encoding="utf-8")
            process = MagicMock(pid=111)
            process.poll.return_value = 0
            cleanup = backend_process_registry.terminate_backend_process(
                workspace=root,
                process=process,
                pid_file=pid_file,
            )
            preserved_pid = pid_file.read_text(encoding="utf-8")

        self.assertTrue(cleanup["success"])
        self.assertTrue(cleanup["pid_file_preserved"])
        self.assertEqual(preserved_pid, "222")

    def test_launch_project_returns_acceptance_request_after_successful_launch(self) -> None:
        backend_result = {
            "status": "running",
            "message": "backend ok",
            "server": {"pid": 321},
            "_process": MagicMock(pid=321),
        }
        frontend_result = {
            "status": "running",
            "message": "ok",
            "preview_url": "http://127.0.0.1:80",
            "package_json_path": "/workspace/Frontend/package.json",
            "server": {"pid": 123},
        }
        calls: list[tuple[str, Path]] = []
        with (
            patch(
                "app.graph.nodes.lifecycle.launch_backend_project",
                side_effect=lambda workspace: calls.append(("backend", workspace))
                or backend_result,
            ),
            patch(
                "app.graph.nodes.lifecycle.launch_frontend_project",
                side_effect=lambda workspace: calls.append(("frontend", workspace))
                or frontend_result,
            ),
        ):
            result = launch_project({"workspace": "/workspace"})

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["preview_url"], "http://127.0.0.1:80")
        self.assertEqual(result["acceptance_request"]["preview_url"], "http://127.0.0.1:80")
        self.assertEqual(result["launch_result"]["backend"]["status"], "running")
        self.assertEqual(result["launch_result"]["frontend"], frontend_result)
        self.assertNotIn("_process", result["launch_result"]["backend"])
        self.assertEqual(
            calls,
            [("backend", Path("/workspace")), ("frontend", Path("/workspace"))],
        )

    def test_launch_project_reports_startup_failure(self) -> None:
        backend_process = MagicMock(pid=321)
        backend_result = {
            "status": "running",
            "message": "backend ok",
            "server": {"pid": 321},
            "_process": backend_process,
        }
        with (
            patch(
                "app.graph.nodes.lifecycle.launch_backend_project",
                return_value=backend_result,
            ) as launch_backend,
            patch(
                "app.graph.nodes.lifecycle.launch_frontend_project",
                return_value={"status": "failed", "message": "未找到前端 package.json。"},
            ) as launch_frontend,
            patch("app.graph.nodes.lifecycle.stop_backend_project") as stop_backend,
        ):
            result = launch_project({"workspace": "/workspace"})

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["launch_result"]["failed_stage"], "frontend_start")
        self.assertIn("项目启动失败", result["acceptance_request"]["message"])
        self.assertEqual(result["preview_url"], "未找到前端 package.json。")
        self.assertEqual(result["launch_result"]["preview_url"], result["preview_url"])
        self.assertEqual(result["acceptance_request"]["preview_url"], result["preview_url"])
        launch_backend.assert_called_once_with(Path("/workspace"))
        launch_frontend.assert_called_once_with(Path("/workspace"))
        stop_backend.assert_called_once_with(backend_result, backend_process)

    def test_launch_project_does_not_start_frontend_when_backend_fails(self) -> None:
        """验证后端失败时工作流立即终止且不调用前端 launcher。"""

        backend_result = {
            "status": "failed",
            "message": "Maven build failed",
            "failed_stage": "backend_build",
        }
        with (
            patch(
                "app.graph.nodes.lifecycle.launch_backend_project",
                return_value=backend_result,
            ) as launch_backend,
            patch(
                "app.graph.nodes.lifecycle.launch_frontend_project"
            ) as launch_frontend,
        ):
            result = launch_project({"workspace": "/workspace"})

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["launch_result"]["failed_stage"], "backend_build")
        self.assertEqual(result["preview_url"], "Maven build failed")
        self.assertEqual(result["launch_result"]["preview_url"], result["preview_url"])
        self.assertIsNone(result["launch_result"]["frontend"])
        launch_backend.assert_called_once_with(Path("/workspace"))
        launch_frontend.assert_not_called()

    def test_acceptance_rejects_implicit_confirmation(self) -> None:
        """缺少结构化验收动作时不能完成交付。"""

        waiting = acceptance({})
        accepted = acceptance({"acceptance_decision": "accepted"})

        self.assertEqual(waiting["status"], "requires_user_input")
        self.assertFalse(waiting["accepted"])
        self.assertEqual(accepted["status"], "completed")
        self.assertTrue(accepted["accepted"])


if __name__ == "__main__":
    unittest.main()
