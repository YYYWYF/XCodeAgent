"""以工作区为粒度监督已启动 Agent Runtime 的健康心跳。"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from app.services.agent_runtime_debug_state import (
    AGENT_RUNTIME_HEARTBEAT_INTERVAL_SECONDS,
    agent_runtime_launch_root,
    mark_agent_runtime_debug_state_offline,
    read_agent_runtime_debug_state,
    write_agent_runtime_debug_state,
)
from app.services.agent_runtime_launch_support import probe_agent_runtime_health


_HEARTBEAT_STOP: dict[str, threading.Event] = {}
_HEARTBEAT_THREADS: dict[str, threading.Thread] = {}
_HEARTBEAT_GUARD = threading.Lock()


def start_agent_runtime_heartbeat(workspace: str | Path) -> None:
    """为指定工作区启动或替换一条不占用启动锁的心跳线程。"""

    workspace_path = Path(workspace).expanduser().resolve(strict=False)
    key = _workspace_key(workspace_path)
    stop_agent_runtime_heartbeat(workspace_path)
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_run_agent_runtime_heartbeat,
        args=(workspace_path, stop_event),
        name=f"agent-runtime-heartbeat:{workspace_path.name}",
        daemon=True,
    )
    with _HEARTBEAT_GUARD:
        _HEARTBEAT_STOP[key] = stop_event
        _HEARTBEAT_THREADS[key] = thread
    thread.start()


def stop_agent_runtime_heartbeat(
    workspace: str | Path,
    *,
    mark_offline: bool = False,
) -> None:
    """停止指定工作区心跳；监督退出时可把 running 改成 offline。"""

    workspace_path = Path(workspace).expanduser().resolve(strict=False)
    key = _workspace_key(workspace_path)
    with _HEARTBEAT_GUARD:
        stop_event = _HEARTBEAT_STOP.pop(key, None)
        thread = _HEARTBEAT_THREADS.pop(key, None)
    if stop_event is not None:
        stop_event.set()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=AGENT_RUNTIME_HEARTBEAT_INTERVAL_SECONDS + 1)
    if mark_offline:
        _mark_supervised_runtime_offline(workspace_path)


def stop_all_agent_runtime_heartbeats(*, mark_offline: bool = True) -> None:
    """FastAPI lifespan 退出时停止全部心跳并标记监督断开。"""

    with _HEARTBEAT_GUARD:
        keys = list(_HEARTBEAT_STOP)
    for key in keys:
        stop_agent_runtime_heartbeat(Path(key), mark_offline=mark_offline)


def tick_agent_runtime_heartbeat(workspace: str | Path) -> dict[str, Any] | None:
    """执行一次探测并回写状态，供测试和心跳线程复用。"""

    runtime_root = agent_runtime_launch_root(workspace)
    current = read_agent_runtime_debug_state(runtime_root)
    if current is None or current.get("status") != "running":
        return current
    host = current.get("host") if isinstance(current.get("host"), str) else "127.0.0.1"
    port = current.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        mark_agent_runtime_debug_state_offline(
            runtime_root,
            message="Agent Runtime 端口不可达。",
            health="ECONNREFUSED",
        )
        return read_agent_runtime_debug_state(runtime_root)
    health, ready = probe_agent_runtime_health(host, port)
    if ready:
        write_agent_runtime_debug_state(
            runtime_root,
            status="running",
            message="Agent Runtime 已启动并就绪。",
            pid=current.get("pid") if isinstance(current.get("pid"), int) else None,
            host=host,
            port=port,
            health=200,
            debug_token=current.get("debugToken"),
        )
        return read_agent_runtime_debug_state(runtime_root)
    message = (
        "Agent Runtime 健康检查超时。"
        if health == "ETIMEDOUT"
        else "Agent Runtime 端口不可达。"
        if health in {"ECONNREFUSED", "ECONNRESET"}
        else "Agent Runtime 健康检查未通过。"
    )
    mark_agent_runtime_debug_state_offline(
        runtime_root,
        message=message,
        health=health,
        pid=current.get("pid"),
    )
    return read_agent_runtime_debug_state(runtime_root)


def _run_agent_runtime_heartbeat(workspace: Path, stop_event: threading.Event) -> None:
    """按固定间隔探测，失败后停心跳；线程异常退出时标 offline。"""

    supervised = True
    try:
        while not stop_event.wait(AGENT_RUNTIME_HEARTBEAT_INTERVAL_SECONDS):
            state = tick_agent_runtime_heartbeat(workspace)
            if state is None or state.get("status") != "running":
                supervised = False
                break
    finally:
        if supervised and not stop_event.is_set():
            _mark_supervised_runtime_offline(workspace)


def _mark_supervised_runtime_offline(workspace: Path) -> None:
    """心跳监督结束且未确认停止 sidecar 时写入 offline。"""

    runtime_root = agent_runtime_launch_root(workspace)
    current = read_agent_runtime_debug_state(runtime_root)
    if current is None or current.get("status") != "running":
        return
    mark_agent_runtime_debug_state_offline(
        runtime_root,
        message="Agent Runtime 监督已断开。",
    )


def _workspace_key(workspace: Path) -> str:
    """生成跨平台一致的工作区心跳登记键。"""

    return os.path.normcase(str(workspace.expanduser().resolve(strict=False)))
