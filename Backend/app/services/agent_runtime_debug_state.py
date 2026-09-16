"""持久化工作区当前 Agent Runtime 启动阶段与心跳探测状态。"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


AGENT_RUNTIME_DEBUG_STATE_FILENAME = "agent-runtime-debug.json"
AGENT_RUNTIME_LEGACY_PID_FILENAME = "agent-runtime.pid"
AGENT_RUNTIME_HEARTBEAT_INTERVAL_SECONDS = 5
AGENT_RUNTIME_STALE_RUNNING_SECONDS = 15
AGENT_RUNTIME_DISPLAY_TIMEZONE = timezone(timedelta(hours=8))
_UNSET = object()
_STATE_LOCKS: dict[str, threading.Lock] = {}
_STATE_LOCKS_GUARD = threading.Lock()


def agent_runtime_launch_root(workspace: str | Path) -> Path:
    """返回工作区 Runtime 启动状态与日志所在目录。"""

    return Path(workspace).expanduser().resolve(strict=False) / ".xcodeagent" / "runtime" / "launch"


def update_time_now_iso() -> str:
    """生成状态文件使用的 UTC+8 毫秒时间戳。"""

    return datetime.now(AGENT_RUNTIME_DISPLAY_TIMEZONE).isoformat(timespec="milliseconds")


def read_agent_runtime_debug_state(runtime_root: Path) -> dict[str, Any] | None:
    """读取当前调试状态对象；缺失或损坏时返回空。"""

    state_path = runtime_root / AGENT_RUNTIME_DEBUG_STATE_FILENAME
    if not state_path.is_file():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def read_agent_runtime_debug_port(runtime_root: Path) -> int | None:
    """读取该工作区上次有效调试端口，缺失或损坏时返回空。"""

    return _positive_port((read_agent_runtime_debug_state(runtime_root) or {}).get("port"))


def read_agent_runtime_debug_pid(runtime_root: Path) -> int | None:
    """读取状态文件中的 Runtime PID，供进程恢复使用。"""

    return _positive_pid((read_agent_runtime_debug_state(runtime_root) or {}).get("pid"))


def write_agent_runtime_debug_state(
    runtime_root: Path,
    *,
    status: str,
    message: str,
    pid: int | None = None,
    host: str | None = None,
    port: int | None = None,
    health: int | str | None = None,
    debug_token: Any = _UNSET,
) -> Path:
    """按当前契约原子写入状态文件，并刷新 updateTime。"""

    with _state_lock(runtime_root):
        current = read_agent_runtime_debug_state(runtime_root) or {}
        payload = {
            "service": "agent-runtime",
            "status": status,
            "message": message,
            "updateTime": update_time_now_iso(),
            "pid": _positive_pid(pid),
            "host": host if isinstance(host, str) and host.strip() else None,
            "port": _positive_port(port),
            "health": _normalize_health(health),
            "debugToken": _resolve_debug_token(
                status=status,
                health=_normalize_health(health),
                requested=debug_token,
                current=current.get("debugToken"),
            ),
        }
        state_path = runtime_root / AGENT_RUNTIME_DEBUG_STATE_FILENAME
        _write_private_json(state_path, payload)
        return state_path


def mark_agent_runtime_debug_state_stopped(runtime_root: Path) -> None:
    """仅在调试状态文件已存在时记录停止状态并移除失效 Token。"""

    if not (runtime_root / AGENT_RUNTIME_DEBUG_STATE_FILENAME).is_file():
        return
    current = read_agent_runtime_debug_state(runtime_root) or {}
    write_agent_runtime_debug_state(
        runtime_root,
        status="stopped",
        message="Agent Runtime 已停止。",
        pid=None,
        host=current.get("host") if isinstance(current.get("host"), str) else None,
        port=_positive_port(current.get("port")),
        health=None,
        debug_token=None,
    )


def mark_agent_runtime_debug_state_offline(
    runtime_root: Path,
    *,
    message: str,
    health: Any = _UNSET,
    pid: Any = _UNSET,
    clear_pid: bool = False,
) -> None:
    """监督断开或探测失败时写入 offline，按 health 决定是否保留 Token。"""

    current = read_agent_runtime_debug_state(runtime_root) or {}
    next_pid = None if clear_pid else (current.get("pid") if pid is _UNSET else pid)
    next_health = current.get("health") if health is _UNSET else health
    write_agent_runtime_debug_state(
        runtime_root,
        status="offline",
        message=message,
        pid=_positive_pid(next_pid),
        host=current.get("host") if isinstance(current.get("host"), str) else None,
        port=_positive_port(current.get("port")),
        health=next_health,
    )


def reconcile_stale_running_agent_runtime_debug_state(
    runtime_root: Path,
) -> dict[str, Any] | None:
    """把超过约 15 秒未刷新的 running 改写成 offline，表示监督已断开。"""

    current = read_agent_runtime_debug_state(runtime_root)
    if current is None or current.get("status") != "running":
        return current
    if not _update_time_is_stale(current.get("updateTime")):
        return current
    mark_agent_runtime_debug_state_offline(
        runtime_root,
        message="Agent Runtime 监督已断开。",
        health=current.get("health"),
        pid=current.get("pid"),
    )
    return read_agent_runtime_debug_state(runtime_root)


def reconcile_stale_running_agent_runtime_debug_state_for_workspace(
    workspace: str | Path,
) -> None:
    """在 Attach / 读取生命周期时回收指定工作区过期的 running 状态。"""

    runtime_root = agent_runtime_launch_root(workspace)
    if not (runtime_root / AGENT_RUNTIME_DEBUG_STATE_FILENAME).is_file():
        return
    reconcile_stale_running_agent_runtime_debug_state(runtime_root)


def discard_legacy_agent_runtime_pid_file(runtime_root: Path) -> None:
    """删除历史 PID 文件，恢复进程时不再把它当权威来源。"""

    (runtime_root / AGENT_RUNTIME_LEGACY_PID_FILENAME).unlink(missing_ok=True)


def failed_stage_for_status(status: str) -> str | None:
    """把工作区 JSON 的融合 status 映射为既有 AG-UI failedStage。"""

    return {
        "validation_failed": "agent_runtime_validation",
        "cleanup_failed": "agent_runtime_cleanup",
        "install_failed": "agent_runtime_install",
        "start_failed": "agent_runtime_start",
        "debug_state_failed": "agent_runtime_debug_state",
        "offline": "agent_runtime_start",
    }.get(status)


def _resolve_debug_token(
    *,
    status: str,
    health: int | str | None,
    requested: Any,
    current: Any,
) -> str | None:
    """按运行/离线规则决定是否保留本次调试 Token。"""

    current_token = current if isinstance(current, str) and current else None
    if requested is _UNSET:
        requested_token = current_token
    elif isinstance(requested, str) and requested:
        requested_token = requested
    else:
        requested_token = None
    if status == "running":
        return requested_token
    if status == "stopped":
        return None
    if status == "offline":
        return requested_token if health == 200 else None
    return None


def _update_time_is_stale(value: Any) -> bool:
    """按绝对时间判断 updateTime 是否早于三个心跳间隔。"""

    if not isinstance(value, str) or not value.strip():
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=AGENT_RUNTIME_DISPLAY_TIMEZONE)
    age = datetime.now(UTC) - parsed.astimezone(UTC)
    return age >= timedelta(seconds=AGENT_RUNTIME_STALE_RUNNING_SECONDS)


def _normalize_health(value: Any) -> int | str | None:
    """只允许空值、归一化 TCP 错误名或 HTTP 状态码整数。"""

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 100 <= value <= 599:
        return value
    if isinstance(value, str) and value in {"ECONNREFUSED", "ETIMEDOUT", "ECONNRESET"}:
        return value
    return None


def _positive_port(value: Any) -> int | None:
    """校验 1–65535 的端口。"""

    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= 65535 else None


def _positive_pid(value: Any) -> int | None:
    """校验可用于恢复的正整数 PID。"""

    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _state_lock(runtime_root: Path) -> threading.Lock:
    """返回指定启动目录的状态文件写入锁。"""

    key = os.path.normcase(str(runtime_root.expanduser().resolve(strict=False)))
    with _STATE_LOCKS_GUARD:
        return _STATE_LOCKS.setdefault(key, threading.Lock())


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    """在同目录原子写入仅当前用户可读写的 JSON 文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(
        temporary_path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary_path, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
