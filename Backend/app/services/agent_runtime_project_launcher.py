"""按工作区启动和停止生成应用的 Python Agent Runtime。"""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.agent_runtime_debug_state import (
    mark_agent_runtime_debug_state_stopped,
    read_agent_runtime_debug_port,
    write_running_agent_runtime_debug_state,
)
from app.services.agent_runtime_launch_support import (
    agent_runtime_environment as _agent_runtime_environment,
    allocate_loopback_port as _allocate_loopback_port,
    run_agent_runtime_install as _run_agent_runtime_install,
    start_agent_runtime_server as _start_agent_runtime_server,
    wait_for_agent_runtime_ready as _wait_for_agent_runtime_ready,
)
from app.services.agent_runtime_process_registry import (
    agent_runtime_launch_lock,
    register_agent_runtime_process,
    stop_previous_agent_runtime_process,
    terminate_agent_runtime_process,
)
from app.services.application_template_generation import (
    TEMPLATE_GENERATION_MANIFEST_RELATIVE_PATH,
)


class AgentRuntimeLaunchError(ValueError):
    """表示 Agent Runtime 启动前置契约无效。"""


def agent_runtime_launch_required(workspace_path: str | Path) -> bool:
    """只根据模板 manifest 判断当前工作区是否需要启动 Agent Runtime。"""

    root = Path(workspace_path).expanduser().resolve()
    manifest_path = root / TEMPLATE_GENERATION_MANIFEST_RELATIVE_PATH
    if not manifest_path.is_file():
        if (root / "agent-runtime").exists():
            raise AgentRuntimeLaunchError(
                "工作区存在 agent-runtime，但缺少模板生成 manifest，无法确认是否应启动。"
            )
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AgentRuntimeLaunchError("模板生成 manifest 损坏或无法读取。") from exc
    steps = manifest.get("steps") if isinstance(manifest, dict) else None
    download = steps.get("download") if isinstance(steps, dict) else None
    targets = download.get("targets") if isinstance(download, dict) else None
    target = targets.get("agentRuntime") if isinstance(targets, dict) else None
    if not isinstance(target, dict) or not isinstance(target.get("required"), bool):
        raise AgentRuntimeLaunchError("模板生成 manifest 缺少有效的 agentRuntime 目标。")
    if target["required"] and target.get("status") != "succeeded":
        raise AgentRuntimeLaunchError("必需的 Agent Runtime 模板尚未成功初始化。")
    if not target["required"]:
        if target.get("status") != "skipped":
            raise AgentRuntimeLaunchError("非必需 Agent Runtime 的 manifest 状态必须为 skipped。")
        if (root / "agent-runtime").exists():
            raise AgentRuntimeLaunchError(
                "manifest 标记 Agent Runtime 非必需，但工作区仍存在 agent-runtime。"
            )
    return target["required"]


def launch_agent_runtime_project(
    workspace_path: str | Path,
    *,
    settings: Settings | None = None,
    include_debug_access: bool = False,
) -> dict[str, Any]:
    """安装依赖并以每次启动独立的内部凭据运行 loopback Runtime。"""

    root = Path(workspace_path).expanduser().resolve()
    agent_runtime_root = root / "agent-runtime"
    pyproject_path = agent_runtime_root / "pyproject.toml"
    runtime_root = root / ".xcodeagent" / "runtime" / "launch"
    if agent_runtime_root.is_symlink() or not pyproject_path.is_file():
        return _failed_agent_runtime_launch(
            "未找到有效的 Agent Runtime 工程：agent-runtime/pyproject.toml。",
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            failed_stage="agent_runtime_validation",
        )
    uv_command = shutil.which("uv")
    if not uv_command:
        return _failed_agent_runtime_launch(
            "未找到 Agent Runtime 包管理器命令：uv。",
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            failed_stage="agent_runtime_validation",
        )

    runtime_root.mkdir(parents=True, exist_ok=True)
    with agent_runtime_launch_lock(root):
        return _launch_agent_runtime_project_locked(
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            uv_command=uv_command,
            settings=settings or Settings.from_env(),
            include_debug_access=include_debug_access,
        )


def _launch_agent_runtime_project_locked(
    *,
    root: Path,
    agent_runtime_root: Path,
    runtime_root: Path,
    uv_command: str,
    settings: Settings,
    include_debug_access: bool,
) -> dict[str, Any]:
    """在工作区锁内完成旧进程清理、依赖同步和 Runtime 启动。"""

    prelaunch_cleanup = stop_previous_agent_runtime_process(
        workspace=root,
        agent_runtime_root=agent_runtime_root,
        runtime_root=runtime_root,
    )
    if not prelaunch_cleanup["success"]:
        return _failed_agent_runtime_launch(
            "无法安全停止上一次 Agent Runtime 进程。",
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            failed_stage="agent_runtime_cleanup",
            prelaunch_cleanup=prelaunch_cleanup,
        )
    mark_agent_runtime_debug_state_stopped(runtime_root)

    install = _run_agent_runtime_install(
        workspace=root,
        agent_runtime_root=agent_runtime_root,
        runtime_root=runtime_root,
        uv_command=uv_command,
    )
    if install["returncode"] != 0:
        return _failed_agent_runtime_launch(
            "Agent Runtime 依赖同步失败。",
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            failed_stage="agent_runtime_install",
            install=install,
            prelaunch_cleanup=prelaunch_cleanup,
        )

    preferred_port = read_agent_runtime_debug_port(runtime_root)
    port = _allocate_loopback_port(preferred_port=preferred_port)
    runtime_url = f"http://127.0.0.1:{port}"
    gateway_token = secrets.token_urlsafe(32)
    environment = _agent_runtime_environment(
        settings,
        port=port,
        gateway_token=gateway_token,
    )
    server_result, process = _start_agent_runtime_server(
        uv_command=uv_command,
        agent_runtime_root=agent_runtime_root,
        runtime_root=runtime_root,
        environment=environment,
    )
    if process is not None:
        register_agent_runtime_process(root, process)
    ready = process is not None and _wait_for_agent_runtime_ready(
        runtime_url, process
    )
    returncode = process.poll() if process is not None else None
    server = {
        **server_result,
        "ready": ready,
        "returncode": returncode,
        "ready_checked_at": datetime.now(UTC).isoformat(),
    }
    if not ready or returncode is not None:
        if returncode is not None:
            message = f"Agent Runtime 进程已退出（退出码：{returncode}）。"
        elif process is None:
            message = "Agent Runtime 启动命令执行失败。"
        else:
            message = "Agent Runtime 健康检查超时。"
        if process is not None:
            server["cleanup"] = terminate_agent_runtime_process(
                workspace=root,
                process=process,
                pid_file=Path(str(server_result["pid_file"])),
            )
        return _failed_agent_runtime_launch(
            message,
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            failed_stage="agent_runtime_start",
            install=install,
            server=server,
            prelaunch_cleanup=prelaunch_cleanup,
        )

    if include_debug_access:
        try:
            write_running_agent_runtime_debug_state(
                runtime_root=runtime_root,
                port=port,
                debug_token=gateway_token,
            )
        except OSError as exc:
            server["cleanup"] = terminate_agent_runtime_process(
                workspace=root,
                process=process,
                pid_file=Path(str(server_result["pid_file"])),
            )
            return _failed_agent_runtime_launch(
                f"Agent Runtime 已启动，但无法记录工作区调试状态：{exc}",
                root=root,
                agent_runtime_root=agent_runtime_root,
                runtime_root=runtime_root,
                failed_stage="agent_runtime_debug_state",
                install=install,
                server=server,
                prelaunch_cleanup=prelaunch_cleanup,
            )

    result = {
        **_base_agent_runtime_payload(root, agent_runtime_root, runtime_root),
        "status": "running",
        "message": "Agent Runtime 已启动并就绪。",
        "install": install,
        "server": server,
        "prelaunch_cleanup": prelaunch_cleanup,
        "failed_stage": None,
        # 仅用于本轮后续失败时回滚，写入节点状态前由聚合启动器移除。
        "_process": process,
    }
    if include_debug_access:
        # 临时调试动作显式请求时才返回；普通预览启动和持久化结果不携带凭据。
        result["_debug_access"] = {
            "runtime_url": runtime_url,
            "gateway_token": gateway_token,
        }
    return result


def stop_agent_runtime_project(
    launch_result: dict[str, Any],
    process: subprocess.Popen[bytes] | None,
) -> dict[str, Any]:
    """停止本次启动的 Runtime，并把清理证据写回启动结果。"""

    root = Path(str(launch_result["workspace"])).expanduser().resolve()
    server = launch_result.get("server")
    if not isinstance(server, dict):
        server = {}
        launch_result["server"] = server
    pid_file_value = server.get("pid_file")
    with agent_runtime_launch_lock(root):
        cleanup = terminate_agent_runtime_process(
            workspace=root,
            process=process,
            pid_file=Path(str(pid_file_value)) if pid_file_value else None,
        )
    server["cleanup"] = cleanup
    launch_result["status"] = "stopped"
    launch_result["message"] = "预览启动失败，已停止本次 Agent Runtime。"
    return cleanup


def stop_workspace_agent_runtime_project(
    workspace_path: str | Path,
) -> dict[str, Any]:
    """停止指定工作区由预览启动器管理的 Agent Runtime。"""

    root = Path(workspace_path).expanduser().resolve()
    agent_runtime_root = root / "agent-runtime"
    runtime_root = root / ".xcodeagent" / "runtime" / "launch"
    runtime_root.mkdir(parents=True, exist_ok=True)
    with agent_runtime_launch_lock(root):
        cleanup = stop_previous_agent_runtime_process(
            workspace=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
        )
        if cleanup.get("success"):
            mark_agent_runtime_debug_state_stopped(runtime_root)
    return {
        "status": "stopped" if cleanup.get("success") else "failed",
        "message": (
            "Agent Runtime 已停止。"
            if cleanup.get("success") and cleanup.get("attempted")
            else "未发现正在运行的 Agent Runtime。"
            if cleanup.get("success")
            else "Agent Runtime 停止失败。"
        ),
        "workspace": str(root),
        "agent_runtime_path": str(agent_runtime_root),
        "runtime_root": str(runtime_root),
        "cleanup": cleanup,
    }


def _base_agent_runtime_payload(
    root: Path,
    agent_runtime_root: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    """构造不包含环境变量和密钥的 Runtime 启动公共字段。"""

    return {
        "workspace": str(root),
        "agent_runtime_path": str(agent_runtime_root),
        "agent_runtime_relative_path": "agent-runtime",
        "runtime_root": str(runtime_root),
    }


def _failed_agent_runtime_launch(
    message: str,
    *,
    root: Path,
    agent_runtime_root: Path,
    runtime_root: Path,
    failed_stage: str,
    install: dict[str, Any] | None = None,
    server: dict[str, Any] | None = None,
    prelaunch_cleanup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造不泄露 fallback 配置的 Runtime 失败结果。"""

    return {
        **_base_agent_runtime_payload(root, agent_runtime_root, runtime_root),
        "status": "failed",
        "message": message,
        "failed_stage": failed_stage,
        "install": install,
        "server": server,
        "prelaunch_cleanup": prelaunch_cleanup,
    }


__all__ = [
    "AgentRuntimeLaunchError",
    "agent_runtime_launch_required",
    "launch_agent_runtime_project",
    "stop_agent_runtime_project",
    "stop_workspace_agent_runtime_project",
]
