from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.services import agent_runtime_process_registry
from app.services.agent_runtime_debug_state import (
    reconcile_stale_running_agent_runtime_debug_state,
    write_agent_runtime_debug_state,
)
from app.services.agent_runtime_heartbeat import tick_agent_runtime_heartbeat
from app.services.agent_runtime_project_launcher import (
    AgentRuntimeLaunchError,
    _agent_runtime_environment,
    agent_runtime_launch_required,
    launch_agent_runtime_project,
)
from app.services.agent_runtime_launch_support import (
    AGENT_RUNTIME_COLD_START_READY_TIMEOUT_SECONDS,
    AGENT_RUNTIME_READY_TIMEOUT_SECONDS,
    allocate_loopback_port,
)
from app.services.project_launcher import launch_project_preview


def _settings() -> Settings:
    """构造不依赖真实模型凭据的 XCodeAgent 测试配置。"""

    return Settings(
        model_base_url="https://xcodeagent.invalid/v1",
        model_api_key="managed-test-key",
        model_name="managed-model [display]",
        model_timeout_seconds=45,
        model_max_retries=3,
        default_temperature=0.4,
        default_max_tokens=8192,
    )


class AgentRuntimeProjectLauncherTests(unittest.TestCase):
    """验证 Runtime 启动判定、白名单注入和预览回滚。"""

    def setUp(self) -> None:
        """清空跨用例共享的 Runtime 进程登记。"""

        agent_runtime_process_registry._AGENT_RUNTIME_PROCESSES.clear()
        agent_runtime_process_registry._AGENT_RUNTIME_LAUNCH_LOCKS.clear()

    def test_launch_requirement_comes_only_from_confirmed_technical_plan(self) -> None:
        """验证是否启动 Runtime 不通过目录存在性反推。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            self.assertFalse(agent_runtime_launch_required(root))
            (root / "agent-runtime").mkdir()
            self.assertFalse(agent_runtime_launch_required(root))

            technical_plan_path = root / ".xcodeagent/plans/technical-plan.json"
            technical_plan_path.parent.mkdir(parents=True)
            technical_plan_path.write_text(
                json.dumps(
                    {
                        "artifact_type": "technical-plan",
                        "confirmation_status": "confirmed",
                        "agent_contracts": [{"agentId": "inventory_assistant"}],
                    }
                ),
                encoding="utf-8",
            )

            self.assertTrue(agent_runtime_launch_required(root))

    def test_managed_environment_uses_fallback_names_and_removes_primary_names(
        self,
    ) -> None:
        """验证父进程普通模型变量不会覆盖工作区 .env。"""

        with patch.dict(
            "app.services.agent_runtime_launch_support.os.environ",
            {"MODEL_API_KEY": "inherited-key", "KEEP_ME": "yes"},
            clear=True,
        ):
            environment = _agent_runtime_environment(
                _settings(),
                port=18110,
                gateway_token="runtime-debug-token",
            )

        self.assertNotIn("MODEL_API_KEY", environment)
        self.assertEqual(environment["KEEP_ME"], "yes")
        self.assertEqual(environment["AGENT_RUNTIME_HOST"], "127.0.0.1")
        self.assertEqual(environment["AGENT_RUNTIME_PORT"], "18110")
        self.assertEqual(
            environment["AGENT_RUNTIME_GATEWAY_TOKEN"],
            "runtime-debug-token",
        )
        self.assertEqual(
            environment["XCODEAGENT_FALLBACK_MODEL_NAME"],
            "openai:managed-model",
        )
        self.assertEqual(
            environment["XCODEAGENT_FALLBACK_MODEL_API_KEY"],
            "managed-test-key",
        )

    def test_port_allocator_reuses_free_workspace_port(self) -> None:
        """验证工作区上次端口仍空闲时保持端口不变。"""

        preferred_context = MagicMock()
        preferred_socket = preferred_context.__enter__.return_value
        with patch(
            "app.services.agent_runtime_launch_support.socket.socket",
            return_value=preferred_context,
        ):
            allocated_port = allocate_loopback_port(preferred_port=18110)

        self.assertEqual(allocated_port, 18110)
        preferred_socket.setsockopt.assert_called_once_with(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1,
        )
        preferred_socket.bind.assert_called_once_with(("127.0.0.1", 18110))

    def test_port_allocator_replaces_port_owned_by_another_service(self) -> None:
        """验证上次端口仍被其他监听者占用时改用新端口。"""

        preferred_context = MagicMock()
        preferred_socket = preferred_context.__enter__.return_value
        preferred_socket.bind.side_effect = OSError("address already in use")
        fallback_context = MagicMock()
        fallback_socket = fallback_context.__enter__.return_value
        fallback_socket.getsockname.return_value = ("127.0.0.1", 18111)
        with patch(
            "app.services.agent_runtime_launch_support.socket.socket",
            side_effect=[preferred_context, fallback_context],
        ):
            allocated_port = allocate_loopback_port(
                preferred_port=18110
            )

        self.assertEqual(allocated_port, 18111)
        fallback_socket.bind.assert_called_once_with(("127.0.0.1", 0))

    def test_launch_result_never_contains_model_secret(self) -> None:
        """验证模型密钥只进入子进程环境，不进入可持久化启动结果。"""

        fake_process = SimpleNamespace(pid=12345, poll=lambda: None)
        install = {
            "returncode": 0,
            "stdout_log": "/tmp/stdout.log",
            "stderr_log": "/tmp/stderr.log",
        }
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            runtime = root / "agent-runtime"
            runtime.mkdir()
            (runtime / "pyproject.toml").write_text("[project]\nname='test'\n")
            with (
                patch(
                    "app.services.agent_runtime_project_launcher.resolve_uv_command",
                    return_value="/usr/local/bin/uv",
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.stop_previous_agent_runtime_process",
                    return_value={"success": True, "attempted": False},
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._run_agent_runtime_install",
                    return_value=install,
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._allocate_loopback_port",
                    return_value=18110,
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._start_agent_runtime_server",
                    return_value=({"pid": 12345}, fake_process),
                ) as start,
                patch(
                    "app.services.agent_runtime_project_launcher._wait_for_agent_runtime_ready",
                    return_value=(True, 200),
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.start_agent_runtime_heartbeat"
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.secrets.token_urlsafe",
                    return_value="generated-runtime-token",
                ),
            ):
                result = launch_agent_runtime_project(root, settings=_settings())
            state = json.loads(
                (
                    root / ".xcodeagent/runtime/launch/agent-runtime-debug.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(result["status"], "running")
        self.assertNotIn("managed-test-key", repr(result))
        self.assertNotIn("generated-runtime-token", repr(result))
        self.assertIsNone(state["debugToken"])
        self.assertNotIn("managed-test-key", json.dumps(state))
        self.assertEqual(
            start.call_args.kwargs["environment"][
                "XCODEAGENT_FALLBACK_MODEL_API_KEY"
            ],
            "managed-test-key",
        )

    def test_debug_launch_records_current_runtime_access_in_workspace(self) -> None:
        """验证显式调试启动把当前端口和临时 Token 写入受管状态文件。"""

        fake_process = SimpleNamespace(pid=12345, poll=lambda: None)
        install = {"returncode": 0, "stdout_log": "", "stderr_log": ""}
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            runtime = root / "agent-runtime"
            runtime.mkdir()
            (runtime / "pyproject.toml").write_text("[project]\nname='test'\n")
            with (
                patch(
                    "app.services.agent_runtime_project_launcher.resolve_uv_command",
                    return_value="/usr/local/bin/uv",
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.stop_previous_agent_runtime_process",
                    return_value={"success": True, "attempted": False},
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._run_agent_runtime_install",
                    return_value=install,
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._allocate_loopback_port",
                    return_value=18110,
                ) as allocate_port,
                patch(
                    "app.services.agent_runtime_project_launcher._start_agent_runtime_server",
                    return_value=({"pid": 12345}, fake_process),
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._wait_for_agent_runtime_ready",
                    return_value=(True, 200),
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.start_agent_runtime_heartbeat"
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.secrets.token_urlsafe",
                    return_value="generated-runtime-token",
                ),
            ):
                result = launch_agent_runtime_project(
                    root,
                    settings=_settings(),
                    include_debug_access=True,
                )

            state_path = (
                root
                / ".xcodeagent/runtime/launch/agent-runtime-debug.json"
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            leftover_pid = (
                root / ".xcodeagent/runtime/launch/agent-runtime.pid"
            ).exists()

        self.assertEqual(result["status"], "running")
        allocate_port.assert_called_once_with(preferred_port=None)
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["service"], "agent-runtime")
        self.assertEqual(state["port"], 18110)
        self.assertEqual(state["pid"], 12345)
        self.assertEqual(state["health"], 200)
        self.assertEqual(state["debugToken"], "generated-runtime-token")
        self.assertIn("updateTime", state)
        self.assertTrue(str(state["updateTime"]).endswith("+08:00"))
        self.assertNotIn("errorCode", state)
        self.assertNotIn("reused", state)
        self.assertNotIn("stage", state)
        self.assertNotIn("failedStage", state)
        self.assertNotIn("logs", state)
        self.assertFalse(leftover_pid)

    def test_pid_recovery_accepts_runtime_command_with_matching_cwd(self) -> None:
        """验证后端重启后可通过进程工作目录识别旧 Runtime。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace).resolve()
            runtime = root / "agent-runtime"
            runtime.mkdir()
            runtime_root = root / ".xcodeagent/runtime/launch"
            runtime_root.mkdir(parents=True)
            write_agent_runtime_debug_state(
                runtime_root,
                status="offline",
                message="Agent Runtime 监督已断开。",
                pid=12345,
                host="127.0.0.1",
                port=18110,
                health=200,
            )
            (runtime_root / "agent-runtime.pid").write_text("99999", encoding="utf-8")

            def mark_terminated(pid: int, cleanup: dict[str, object]) -> None:
                """模拟成功结束已经通过身份校验的恢复进程。"""

                self.assertEqual(pid, 12345)
                cleanup["terminated"] = True
                cleanup["success"] = True

            with (
                patch.object(
                    agent_runtime_process_registry,
                    "_pid_is_running",
                    return_value=True,
                ),
                patch.object(
                    agent_runtime_process_registry,
                    "_query_process_command",
                    return_value=(f"uv --directory {runtime} run agent-runtime", None),
                ),
                patch.object(
                    agent_runtime_process_registry,
                    "_query_process_working_directory",
                    return_value=(str(runtime), None),
                ),
                patch.object(
                    agent_runtime_process_registry,
                    "_terminate_recovered_pid",
                    side_effect=mark_terminated,
                ),
            ):
                cleanup = (
                    agent_runtime_process_registry.stop_previous_agent_runtime_process(
                        workspace=root,
                        agent_runtime_root=runtime,
                        runtime_root=runtime_root,
                    )
                )

            leftover_pid = (runtime_root / "agent-runtime.pid").exists()

        self.assertTrue(cleanup["success"])
        self.assertTrue(cleanup["identity_matched"])
        self.assertEqual(cleanup["source"], "debug_state")
        self.assertFalse(leftover_pid)

    def test_pid_recovery_rejects_runtime_command_from_another_cwd(self) -> None:
        """验证同名 Runtime 位于其他工作区时仍会拒绝终止。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace).resolve()
            runtime = root / "agent-runtime"
            runtime.mkdir()
            runtime_root = root / ".xcodeagent/runtime/launch"
            runtime_root.mkdir(parents=True)
            write_agent_runtime_debug_state(
                runtime_root,
                status="offline",
                message="Agent Runtime 监督已断开。",
                pid=12345,
                host="127.0.0.1",
                port=18110,
                health=200,
            )

            with (
                patch.object(
                    agent_runtime_process_registry,
                    "_pid_is_running",
                    return_value=True,
                ),
                patch.object(
                    agent_runtime_process_registry,
                    "_query_process_command",
                    return_value=(
                        f"uv --directory {root / 'another-runtime'} run agent-runtime",
                        None,
                    ),
                ),
                patch.object(
                    agent_runtime_process_registry,
                    "_query_process_working_directory",
                    return_value=(str(root / "another-runtime"), None),
                ),
                patch.object(
                    agent_runtime_process_registry,
                    "_terminate_recovered_pid",
                ) as terminate,
            ):
                cleanup = (
                    agent_runtime_process_registry.stop_previous_agent_runtime_process(
                        workspace=root,
                        agent_runtime_root=runtime,
                        runtime_root=runtime_root,
                    )
                )

        self.assertFalse(cleanup["success"])
        self.assertFalse(cleanup["identity_matched"])
        terminate.assert_not_called()

    def test_runtime_failure_stops_started_backend_and_skips_frontend(self) -> None:
        """验证必需 Runtime 失败时回滚 Java 后端且不启动前端。"""

        backend_process = MagicMock(pid=321)
        backend = {
            "status": "running",
            "message": "backend ready",
            "_process": backend_process,
        }
        runtime = {
            "status": "failed",
            "message": "runtime failed",
            "failed_stage": "agent_runtime_start",
        }
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace).resolve()
            with (
                patch(
                    "app.services.project_launch_stages.find_backend_project_root",
                    return_value=root / "backend",
                ),
                patch(
                    "app.services.project_launch_stages.agent_runtime_launch_required",
                    return_value=True,
                ),
                patch(
                    "app.services.project_launch_stages.launch_backend_project",
                    return_value=backend,
                ),
                patch(
                    "app.services.project_launch_stages.launch_agent_runtime_project",
                    return_value=runtime,
                ),
                patch(
                    "app.services.project_launch_stages.stop_backend_project"
                ) as stop_backend,
                patch(
                    "app.services.project_launch_stages.launch_frontend_project"
                ) as launch_frontend,
            ):
                result = launch_project_preview(root)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_stage"], "agent_runtime_start")
        stop_backend.assert_called_once_with(backend, backend_process)
        launch_frontend.assert_not_called()

    def test_install_failure_persists_install_failed_status(self) -> None:
        """验证依赖同步失败会在状态文件写下 install_failed。"""

        install = {
            "returncode": 1,
            "stdout_log": "agent-runtime-install.stdout.log",
            "stderr_log": "agent-runtime-install.stderr.log",
        }
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            runtime = root / "agent-runtime"
            runtime.mkdir()
            (runtime / "pyproject.toml").write_text("[project]\nname='test'\n")
            statuses: list[str] = []

            def capture_status(runtime_root: Path, **kwargs):  # type: ignore[no-untyped-def]
                """记录启动器写入的融合 status，并继续落盘。"""

                statuses.append(str(kwargs.get("status")))
                return write_agent_runtime_debug_state(runtime_root, **kwargs)

            with (
                patch(
                    "app.services.agent_runtime_project_launcher.resolve_uv_command",
                    return_value="/usr/local/bin/uv",
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.stop_previous_agent_runtime_process",
                    return_value={"success": True, "attempted": False},
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._run_agent_runtime_install",
                    return_value=install,
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.write_agent_runtime_debug_state",
                    side_effect=capture_status,
                ),
            ):
                result = launch_agent_runtime_project(root, settings=_settings())
            state = json.loads(
                (
                    root / ".xcodeagent/runtime/launch/agent-runtime-debug.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_stage"], "agent_runtime_install")
        self.assertEqual(
            statuses,
            ["cleaning", "validating", "installing", "install_failed"],
        )
        self.assertEqual(state["status"], "install_failed")
        self.assertIsNone(state["health"])
        self.assertNotIn("unhealthy", json.dumps(state))
        self.assertIn("agent-runtime-install.stderr.log", install["stderr_log"])

    def test_health_timeout_persists_offline_status(self) -> None:
        """验证 /health 超时写入 offline 而不是 start_failed。"""

        fake_process = SimpleNamespace(pid=12345, poll=lambda: None)
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            runtime = root / "agent-runtime"
            runtime.mkdir()
            (runtime / "pyproject.toml").write_text("[project]\nname='test'\n")
            with (
                patch(
                    "app.services.agent_runtime_project_launcher.resolve_uv_command",
                    return_value="/usr/local/bin/uv",
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.stop_previous_agent_runtime_process",
                    return_value={"success": True, "attempted": False},
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._run_agent_runtime_install",
                    return_value={"returncode": 0},
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._allocate_loopback_port",
                    return_value=18110,
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._start_agent_runtime_server",
                    return_value=({"pid": 12345}, fake_process),
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._wait_for_agent_runtime_ready",
                    return_value=(False, "ETIMEDOUT"),
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.terminate_agent_runtime_process",
                    return_value={"success": True},
                ),
            ):
                result = launch_agent_runtime_project(root, settings=_settings())
            state = json.loads(
                (
                    root / ".xcodeagent/runtime/launch/agent-runtime-debug.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_stage"], "agent_runtime_start")
        self.assertEqual(state["status"], "offline")
        self.assertEqual(state["health"], "ETIMEDOUT")
        self.assertIsNone(state["pid"])
        self.assertIsNone(state["debugToken"])

    def test_cold_start_launch_uses_extended_ready_timeout(self) -> None:
        """验证 .venv 缺失的冷启动使用更长的就绪宽限，热启动沿用标准窗口。"""

        observed_timeouts: list[float] = []

        def capture_wait(url: str, process, **kwargs) -> tuple[bool, int]:  # type: ignore[no-untyped-def]
            """记录本次启动传入的就绪宽限，并直接判定就绪。"""

            observed_timeouts.append(float(kwargs.get("timeout_seconds", 0)))
            return True, 200

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            runtime = root / "agent-runtime"
            runtime.mkdir()
            (runtime / "pyproject.toml").write_text("[project]\nname='test'\n")
            fake_process = SimpleNamespace(pid=12345, poll=lambda: None)
            with (
                patch(
                    "app.services.agent_runtime_project_launcher.resolve_uv_command",
                    return_value="/usr/local/bin/uv",
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.stop_previous_agent_runtime_process",
                    return_value={"success": True, "attempted": False},
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._run_agent_runtime_install",
                    return_value={"returncode": 0},
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._allocate_loopback_port",
                    return_value=18110,
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._start_agent_runtime_server",
                    return_value=({"pid": 12345}, fake_process),
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._wait_for_agent_runtime_ready",
                    side_effect=capture_wait,
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.start_agent_runtime_heartbeat"
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.secrets.token_urlsafe",
                    return_value="generated-runtime-token",
                ),
            ):
                cold_result = launch_agent_runtime_project(root, settings=_settings())
                # 第二次启动前 .venv 已存在，应回到标准 30 秒窗口。
                (runtime / ".venv").mkdir()
                warm_result = launch_agent_runtime_project(root, settings=_settings())

        self.assertEqual(cold_result["status"], "running")
        self.assertEqual(warm_result["status"], "running")
        self.assertTrue(cold_result["server"]["cold_start"])
        self.assertEqual(
            observed_timeouts[0],
            AGENT_RUNTIME_COLD_START_READY_TIMEOUT_SECONDS,
        )
        self.assertFalse(warm_result["server"]["cold_start"])
        self.assertEqual(
            observed_timeouts[1],
            AGENT_RUNTIME_READY_TIMEOUT_SECONDS,
        )

    def test_missing_uv_tries_official_install_before_validation_failed(self) -> None:
        """验证找不到 uv 时先走官方安装，仍失败才写 validation_failed。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            runtime = root / "agent-runtime"
            runtime.mkdir()
            (runtime / "pyproject.toml").write_text("[project]\nname='test'\n")
            with (
                patch(
                    "app.services.agent_runtime_project_launcher.resolve_uv_command",
                    return_value=None,
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.install_uv_with_official_script",
                    return_value={"succeeded": False},
                ) as install_uv,
                patch(
                    "app.services.agent_runtime_project_launcher.stop_previous_agent_runtime_process",
                    return_value={"success": True, "attempted": False},
                ),
            ):
                result = launch_agent_runtime_project(root, settings=_settings())
            state = json.loads(
                (
                    root / ".xcodeagent/runtime/launch/agent-runtime-debug.json"
                ).read_text(encoding="utf-8")
            )

        install_uv.assert_called_once()
        self.assertEqual(result["failed_stage"], "agent_runtime_validation")
        self.assertEqual(state["status"], "validation_failed")

    def test_missing_runtime_project_is_provisioned_from_git_template(self) -> None:
        """工作区缺少 pyproject.toml 时先从 Git 模板补齐，再继续启动。"""

        fake_process = SimpleNamespace(pid=12345, poll=lambda: None)
        install = {"returncode": 0, "stdout_log": "", "stderr_log": ""}
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)

            def provision_runtime(_self, workspace_path):  # type: ignore[no-untyped-def]
                """模拟 Git 模板把 pyproject.toml 写入工作区。"""

                runtime = Path(workspace_path) / "agent-runtime"
                runtime.mkdir()
                (runtime / "pyproject.toml").write_text("[project]\nname='test'\n")
                return True

            with (
                patch(
                    "app.services.agent_runtime_project_launcher.GitTemplatePackageBuilder.materialize_missing_agent_runtime_root",
                    new=provision_runtime,
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.resolve_uv_command",
                    return_value="/usr/local/bin/uv",
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.stop_previous_agent_runtime_process",
                    return_value={"success": True, "attempted": False},
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._run_agent_runtime_install",
                    return_value=install,
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._allocate_loopback_port",
                    return_value=18110,
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._start_agent_runtime_server",
                    return_value=({"pid": 12345}, fake_process),
                ),
                patch(
                    "app.services.agent_runtime_project_launcher._wait_for_agent_runtime_ready",
                    return_value=(True, 200),
                ),
                patch(
                    "app.services.agent_runtime_project_launcher.start_agent_runtime_heartbeat"
                ),
            ):
                result = launch_agent_runtime_project(root, settings=_settings())
            self.assertEqual(result["status"], "running")
            self.assertTrue((root / "agent-runtime/pyproject.toml").is_file())

    def test_stale_running_status_is_rewritten_offline(self) -> None:
        """验证超过约 15 秒未刷新的 running 会被改成 offline。"""

        with tempfile.TemporaryDirectory() as workspace:
            runtime_root = Path(workspace) / ".xcodeagent/runtime/launch"
            runtime_root.mkdir(parents=True)
            write_agent_runtime_debug_state(
                runtime_root,
                status="running",
                message="Agent Runtime 已启动并就绪。",
                pid=18432,
                host="127.0.0.1",
                port=61245,
                health=200,
                debug_token="keep-token",
            )
            stale = json.loads(
                (runtime_root / "agent-runtime-debug.json").read_text(encoding="utf-8")
            )
            stale["updateTime"] = "2026-09-16T09:00:00.000+08:00"
            (runtime_root / "agent-runtime-debug.json").write_text(
                json.dumps(stale), encoding="utf-8"
            )
            reconciled = reconcile_stale_running_agent_runtime_debug_state(runtime_root)

        self.assertEqual(reconciled["status"], "offline")
        self.assertEqual(reconciled["health"], 200)
        self.assertEqual(reconciled["pid"], 18432)
        self.assertEqual(reconciled["debugToken"], "keep-token")

    def test_heartbeat_marks_offline_when_port_refused(self) -> None:
        """验证心跳探测到 ECONNREFUSED 后把 running 改成 offline 并清掉 Token。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            runtime_root = root / ".xcodeagent/runtime/launch"
            runtime_root.mkdir(parents=True)
            write_agent_runtime_debug_state(
                runtime_root,
                status="running",
                message="Agent Runtime 已启动并就绪。",
                pid=18432,
                host="127.0.0.1",
                port=61245,
                health=200,
                debug_token="debug-token",
            )
            with patch(
                "app.services.agent_runtime_heartbeat.probe_agent_runtime_health",
                return_value=("ECONNREFUSED", False),
            ):
                state = tick_agent_runtime_heartbeat(root)

        self.assertEqual(state["status"], "offline")
        self.assertEqual(state["health"], "ECONNREFUSED")
        self.assertEqual(state["pid"], 18432)
        self.assertIsNone(state["debugToken"])
        self.assertNotEqual(state["status"], "unhealthy")


if __name__ == "__main__":
    unittest.main()
