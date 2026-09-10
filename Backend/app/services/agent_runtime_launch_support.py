"""提供 Agent Runtime 启动器的环境、依赖、进程和健康检查能力。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from app.config import Settings
from app.services.workspace_process_registry import workspace_process_registry
from app.utils.subprocess_output import subprocess_output_text


AGENT_RUNTIME_INSTALL_TIMEOUT_SECONDS = 300
AGENT_RUNTIME_READY_TIMEOUT_SECONDS = 30
AGENT_RUNTIME_READY_INTERVAL_SECONDS = 0.5
_INHERITED_MODEL_ENVIRONMENT_NAMES = (
    "MODEL_BASE_URL",
    "MODEL_API_KEY",
    "MODEL_NAME",
    "MODEL_TIMEOUT_SECONDS",
    "MODEL_MAX_RETRIES",
    "AGENT_TEMPERATURE",
    "AGENT_MAX_TOKENS",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)


def agent_runtime_environment(
    settings: Settings,
    *,
    port: int,
    gateway_token: str,
) -> dict[str, str]:
    """注入受管监听、内部认证和仅供模板兜底的模型白名单配置。"""

    environment = dict(os.environ)
    for name in _INHERITED_MODEL_ENVIRONMENT_NAMES:
        environment.pop(name, None)
    model_name = settings.model_api_name
    if ":" not in model_name:
        model_name = f"{settings.model_provider}:{model_name}"
    environment.update(
        {
            "AGENT_RUNTIME_HOST": "127.0.0.1",
            "AGENT_RUNTIME_PORT": str(port),
            "AGENT_RUNTIME_GATEWAY_TOKEN": gateway_token,
            "XCODEAGENT_FALLBACK_MODEL_BASE_URL": settings.model_base_url,
            "XCODEAGENT_FALLBACK_MODEL_API_KEY": settings.model_api_key,
            "XCODEAGENT_FALLBACK_MODEL_NAME": model_name,
            "XCODEAGENT_FALLBACK_MODEL_TIMEOUT_SECONDS": str(
                settings.model_timeout_seconds
            ),
            "XCODEAGENT_FALLBACK_MODEL_MAX_RETRIES": str(settings.model_max_retries),
            "XCODEAGENT_FALLBACK_AGENT_TEMPERATURE": str(
                settings.default_temperature
            ),
            "XCODEAGENT_FALLBACK_AGENT_MAX_TOKENS": str(
                settings.default_max_tokens
            ),
        }
    )
    return environment


def run_agent_runtime_install(
    *,
    workspace: Path,
    agent_runtime_root: Path,
    runtime_root: Path,
    uv_command: str,
) -> dict[str, Any]:
    """使用锁文件同步 Runtime 依赖，并把输出写入工作区运行日志。"""

    argv = [uv_command, "sync", "--frozen"]
    started_at = datetime.now(UTC).isoformat()
    try:
        completed = workspace_process_registry.run(
            argv,
            workspace=workspace,
            cwd=str(agent_runtime_root),
            text=True,
            capture_output=True,
            timeout=AGENT_RUNTIME_INSTALL_TIMEOUT_SECONDS,
            check=False,
        )
        stdout = subprocess_output_text(completed.stdout)
        stderr = subprocess_output_text(completed.stderr)
        returncode = completed.returncode
        timed_out = False
        error = None
    except subprocess.TimeoutExpired as exc:
        stdout = subprocess_output_text(exc.stdout)
        stderr = subprocess_output_text(exc.stderr)
        returncode = None
        timed_out = True
        error = None
    except OSError as exc:
        stdout = ""
        stderr = str(exc)
        returncode = None
        timed_out = False
        error = str(exc)
    stdout_path = runtime_root / "agent-runtime-install.stdout.log"
    stderr_path = runtime_root / "agent-runtime-install.stderr.log"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {
        "argv": argv,
        "cwd": str(agent_runtime_root),
        "returncode": returncode,
        "timed_out": timed_out,
        "error": error,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }


def start_agent_runtime_server(
    *,
    uv_command: str,
    agent_runtime_root: Path,
    runtime_root: Path,
    environment: dict[str, str],
) -> tuple[dict[str, Any], subprocess.Popen[bytes] | None]:
    """后台启动 Runtime；环境只传给子进程且不写入返回结果。"""

    argv = [
        uv_command,
        "--directory",
        str(agent_runtime_root),
        "run",
        "agent-runtime",
    ]
    stdout_path = runtime_root / "agent-runtime.stdout.log"
    stderr_path = runtime_root / "agent-runtime.stderr.log"
    stdout = stdout_path.open("ab")
    stderr = stderr_path.open("ab")
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(agent_runtime_root),
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            env=environment,
            start_new_session=os.name == "nt",
        )
    except OSError as exc:
        stdout.close()
        stderr.close()
        return (
            {
                "argv": argv,
                "cwd": str(agent_runtime_root),
                "pid": None,
                "error": str(exc),
                "stdout_log": str(stdout_path),
                "stderr_log": str(stderr_path),
            },
            None,
        )
    stdout.close()
    stderr.close()
    pid_path = runtime_root / "agent-runtime.pid"
    pid_path.write_text(str(process.pid), encoding="utf-8")
    return (
        {
            "argv": argv,
            "cwd": str(agent_runtime_root),
            "pid": process.pid,
            "pid_file": str(pid_path),
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
            "started_at": datetime.now(UTC).isoformat(),
        },
        process,
    )


def allocate_loopback_port(*, preferred_port: int | None = None) -> int:
    """优先复用工作区端口；被其他服务占用时再申请新端口。"""

    if preferred_port is not None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as preferred_server:
            # 允许复用旧 Runtime 已关闭后留下的 TIME_WAIT，但不会绕过真实监听占用。
            preferred_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                preferred_server.bind(("127.0.0.1", preferred_port))
            except OSError:
                pass
            else:
                return preferred_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def wait_for_agent_runtime_ready(
    runtime_url: str,
    process: subprocess.Popen[bytes],
) -> bool:
    """监督子进程并等待 Runtime 健康契约就绪。"""

    deadline = time.monotonic() + AGENT_RUNTIME_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if agent_runtime_is_ready(runtime_url):
            return True
        time.sleep(AGENT_RUNTIME_READY_INTERVAL_SECONDS)
    return False


def agent_runtime_is_ready(runtime_url: str) -> bool:
    """校验 Runtime /health 的无敏感状态响应。"""

    try:
        with urlopen(f"{runtime_url}/health", timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, HTTPError, URLError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("service") == "agent-runtime"
        and payload.get("status") == "ok"
    )
