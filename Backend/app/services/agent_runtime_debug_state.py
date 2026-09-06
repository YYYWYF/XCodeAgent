"""持久化工作区当前 Agent Runtime 临时调试状态。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


AGENT_RUNTIME_DEBUG_STATE_FILENAME = "agent-runtime-debug.json"


def read_agent_runtime_debug_port(runtime_root: Path) -> int | None:
    """读取该工作区上次有效调试端口，缺失或损坏时返回空。"""

    state_path = runtime_root / AGENT_RUNTIME_DEBUG_STATE_FILENAME
    if not state_path.is_file():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    port = payload.get("port") if isinstance(payload, dict) else None
    if isinstance(port, bool) or not isinstance(port, int):
        return None
    return port if 1 <= port <= 65535 else None


def write_running_agent_runtime_debug_state(
    *,
    runtime_root: Path,
    port: int,
    debug_token: str,
) -> Path:
    """记录当前已通过健康检查的 Runtime 地址和本次临时调试 Token。"""

    payload = {
        "status": "running",
        "service": "agent-runtime",
        "host": "127.0.0.1",
        "port": port,
        "health": {"status": "healthy"},
        "reused": False,
        "errorCode": None,
        "message": "Agent Runtime 已启动",
        "debugToken": debug_token,
    }
    state_path = runtime_root / AGENT_RUNTIME_DEBUG_STATE_FILENAME
    _write_private_json(state_path, payload)
    return state_path


def mark_agent_runtime_debug_state_stopped(runtime_root: Path) -> None:
    """仅在调试状态文件已存在时记录停止状态并移除失效 Token。"""

    state_path = runtime_root / AGENT_RUNTIME_DEBUG_STATE_FILENAME
    if not state_path.is_file():
        return
    try:
        current = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        current = {}
    payload: dict[str, Any] = current if isinstance(current, dict) else {}
    payload.update(
        {
            "status": "stopped",
            "service": "agent-runtime",
            "health": {"status": "stopped"},
            "reused": False,
            "errorCode": None,
            "message": "Agent Runtime 已停止",
            "debugToken": None,
        }
    )
    _write_private_json(state_path, payload)


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
