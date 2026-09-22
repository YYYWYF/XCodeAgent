"""提供 Agent Runtime 启动器的环境、依赖、进程和健康检查能力。"""

from __future__ import annotations

import errno
import json
import os
import socket
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from app.config import Settings
from app.services.workspace_process_registry import workspace_process_registry
from app.utils.subprocess_output import subprocess_output_text


AGENT_RUNTIME_INSTALL_TIMEOUT_SECONDS = 300
AGENT_RUNTIME_READY_TIMEOUT_SECONDS = 30
# 冷启动（.venv 缺失、首次 uv sync 重建）时进程要完成建环境和字节码编译，
# 首次 /health 响应明显更慢，因此单独给一段更长的就绪宽限。
AGENT_RUNTIME_COLD_START_READY_TIMEOUT_SECONDS = 180
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
    "AGENT_RUNTIME_PROFILE",
    "AGENT_RUNTIME_AUTH_ENABLED",
    "AGENT_RUNTIME_AUTH_MODE",
    "AGENT_RUNTIME_ANONYMOUS_SESSION_SECRET",
    "AGENT_RUNTIME_ALLOWED_ORIGINS",
    "AGENT_RUNTIME_DEBUG_TOKEN",
    # Direct 拓扑下本轮不注入网关凭据，必须同时清掉宿主残留，避免旧凭据随环境继承。
    "AGENT_RUNTIME_GATEWAY_TOKEN",
)


def agent_runtime_environment(
    settings: Settings,
    *,
    port: int,
    gateway_token: str,
    direct_auth_enabled: bool | None = None,
    include_debug_access: bool = False,
) -> dict[str, str]:
    """注入受管监听、内部认证和仅供模板兜底的模型白名单配置。

    direct_auth_enabled 为 None 表示由网关托管认证：Runtime 只接受共享内部凭据。
    为布尔值则表示 Direct 拓扑下 Runtime 自己就是公开入口、自行终止认证，此时按
    该开关注入本地认证配置，并把本轮共享随机串复用为匿名会话密钥，不再注入网关凭据；
    include_debug_access 为 True 时额外下发调试令牌。
    """

    environment = dict(os.environ)
    for name in _INHERITED_MODEL_ENVIRONMENT_NAMES:
        environment.pop(name, None)
    model_name = settings.model_api_name
    if ":" not in model_name:
        model_name = f"{settings.model_provider}:{model_name}"
    runtime_environment = {
        "AGENT_RUNTIME_HOST": "127.0.0.1",
        "AGENT_RUNTIME_PORT": str(port),
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
    if direct_auth_enabled is None:
        runtime_environment["AGENT_RUNTIME_GATEWAY_TOKEN"] = gateway_token
    else:
        runtime_environment.update(
            {
                "AGENT_RUNTIME_PROFILE": "local",
                "AGENT_RUNTIME_AUTH_ENABLED": str(direct_auth_enabled).lower(),
                "AGENT_RUNTIME_AUTH_MODE": "local",
                "AGENT_RUNTIME_ANONYMOUS_SESSION_SECRET": gateway_token,
                "AGENT_RUNTIME_ALLOWED_ORIGINS": (
                    "http://127.0.0.1,http://localhost,"
                    "http://127.0.0.1:5173,http://localhost:5173"
                ),
            }
        )
        if include_debug_access:
            runtime_environment["AGENT_RUNTIME_DEBUG_TOKEN"] = gateway_token
    environment.update(runtime_environment)
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
    return (
        {
            "argv": argv,
            "cwd": str(agent_runtime_root),
            "pid": process.pid,
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


def agent_runtime_ready_timeout_seconds(*, cold_start: bool) -> float:
    """冷启动使用更长的就绪宽限；热启动沿用标准 30 秒窗口。"""

    return (
        AGENT_RUNTIME_COLD_START_READY_TIMEOUT_SECONDS
        if cold_start
        else AGENT_RUNTIME_READY_TIMEOUT_SECONDS
    )


def wait_for_agent_runtime_ready(
    runtime_url: str,
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float = AGENT_RUNTIME_READY_TIMEOUT_SECONDS,
) -> tuple[bool, int | str | None]:
    """监督子进程并等待 Runtime 健康契约就绪，同时返回最近一次探测结果。"""

    last_health: int | str | None = None
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False, last_health
        health, ready = probe_agent_runtime_health_url(runtime_url)
        last_health = health
        if ready:
            return True, health
        time.sleep(AGENT_RUNTIME_READY_INTERVAL_SECONDS)
    return False, last_health if last_health is not None else "ETIMEDOUT"


def agent_runtime_is_ready(runtime_url: str) -> bool:
    """校验 Runtime /health 的无敏感状态响应。"""

    _health, ready = probe_agent_runtime_health_url(runtime_url)
    return ready


def probe_agent_runtime_health(
    host: str,
    port: int,
    *,
    timeout: float = 1.0,
) -> tuple[int | str | None, bool]:
    """探测 loopback 端口与 /health，返回标量 health 以及契约是否就绪。"""

    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except TimeoutError:
        return "ETIMEDOUT", False
    except OSError as exc:
        return normalize_agent_runtime_socket_error(exc), False

    try:
        with urlopen(f"http://{host}:{port}/health", timeout=timeout) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            raw = response.read()
    except HTTPError as exc:
        return int(exc.code), False
    except TimeoutError:
        return "ETIMEDOUT", False
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return "ETIMEDOUT", False
        if isinstance(reason, OSError):
            return normalize_agent_runtime_socket_error(reason), False
        return "ETIMEDOUT", False
    except OSError as exc:
        return normalize_agent_runtime_socket_error(exc), False

    body_ok = False
    if status_code == 200:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            payload = None
        body_ok = (
            isinstance(payload, dict)
            and payload.get("service") == "agent-runtime"
            and payload.get("status") == "ok"
        )
    return status_code, body_ok


def probe_agent_runtime_health_url(
    runtime_url: str,
    *,
    timeout: float = 1.0,
) -> tuple[int | str | None, bool]:
    """从 Runtime URL 解析 host/port 后再做健康探测。"""

    parsed = urlparse(runtime_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if port is None:
        return "ECONNREFUSED", False
    return probe_agent_runtime_health(host, port, timeout=timeout)


def normalize_agent_runtime_socket_error(exc: OSError) -> str:
    """把 POSIX errno 与 Windows WSA* 归一成状态文件使用的 TCP 错误名。"""

    code = getattr(exc, "winerror", None) if os.name == "nt" else exc.errno
    if code is None:
        code = exc.errno
    mapping = {
        errno.ECONNREFUSED: "ECONNREFUSED",
        errno.ETIMEDOUT: "ETIMEDOUT",
        errno.ECONNRESET: "ECONNRESET",
        10061: "ECONNREFUSED",
        10060: "ETIMEDOUT",
        10054: "ECONNRESET",
    }
    if code in mapping:
        return mapping[code]
    name = errno.errorcode.get(code or -1, "")
    if name in {"ECONNREFUSED", "ETIMEDOUT", "ECONNRESET"}:
        return name
    detail = str(exc).lower()
    if "timed out" in detail or "timedout" in detail:
        return "ETIMEDOUT"
    if "reset" in detail:
        return "ECONNRESET"
    return "ECONNREFUSED"
