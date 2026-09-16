"""按工作区启动和停止生成应用的 Python Agent Runtime。"""

from __future__ import annotations

import json
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.agent_runtime_debug_state import (
    discard_legacy_agent_runtime_pid_file,
    failed_stage_for_status,
    mark_agent_runtime_debug_state_offline,
    mark_agent_runtime_debug_state_stopped,
    read_agent_runtime_debug_port,
    reconcile_stale_running_agent_runtime_debug_state,
    write_agent_runtime_debug_state,
)
from app.services.agent_runtime_heartbeat import (
    start_agent_runtime_heartbeat,
    stop_agent_runtime_heartbeat,
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
from app.services.agent_runtime_uv import install_uv_with_official_script, resolve_uv_command
from app.services.workspace_bootstrap.git_template_package import GitTemplatePackageBuilder
from app.services.workspace_bootstrap.models import GitTemplateError


class AgentRuntimeLaunchError(ValueError):
    """表示 Agent Runtime 启动前置契约无效。"""


def agent_runtime_launch_required(workspace_path: str | Path) -> bool:
    """只根据已确认 TechnicalPlan 判断当前工作区是否需要启动 Agent Runtime。"""

    root = Path(workspace_path).expanduser().resolve()
    technical_plan_path = root / ".xcodeagent" / "plans" / "technical-plan.json"
    if not technical_plan_path.is_file() or technical_plan_path.is_symlink():
        return False
    try:
        technical_plan = json.loads(technical_plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AgentRuntimeLaunchError("TechnicalPlan 损坏或无法读取。") from exc
    if not isinstance(technical_plan, dict):
        raise AgentRuntimeLaunchError("TechnicalPlan 必须是 JSON 对象。")
    if (
        technical_plan.get("artifact_type") != "technical-plan"
        or technical_plan.get("confirmation_status") != "confirmed"
    ):
        raise AgentRuntimeLaunchError("必须使用已确认的当前 TechnicalPlan 判断 Agent Runtime。")
    contracts = technical_plan.get("agent_contracts")
    if not isinstance(contracts, list):
        raise AgentRuntimeLaunchError("TechnicalPlan.agent_contracts 必须是数组。")
    return bool(contracts)


def launch_agent_runtime_project(
    workspace_path: str | Path,
    *,
    settings: Settings | None = None,
    include_debug_access: bool = False,
) -> dict[str, Any]:
    """安装依赖并以每次启动独立的内部凭据运行 loopback Runtime。"""

    root = Path(workspace_path).expanduser().resolve()
    agent_runtime_root = root / "agent-runtime"
    runtime_root = root / ".xcodeagent" / "runtime" / "launch"
    runtime_root.mkdir(parents=True, exist_ok=True)
    with agent_runtime_launch_lock(root):
        return _launch_agent_runtime_project_locked(
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            settings=settings or Settings.from_env(),
            include_debug_access=include_debug_access,
        )


def _launch_agent_runtime_project_locked(
    *,
    root: Path,
    agent_runtime_root: Path,
    runtime_root: Path,
    settings: Settings,
    include_debug_access: bool,
) -> dict[str, Any]:
    """在工作区锁内按 cleaning → validating → installing → starting 启动 Runtime。"""

    stop_agent_runtime_heartbeat(root)
    reconcile_stale_running_agent_runtime_debug_state(runtime_root)
    preferred_port = read_agent_runtime_debug_port(runtime_root)
    discard_legacy_agent_runtime_pid_file(runtime_root)
    write_agent_runtime_debug_state(
        runtime_root,
        status="cleaning",
        message="正在停止上一次 Agent Runtime。",
    )
    prelaunch_cleanup = stop_previous_agent_runtime_process(
        workspace=root,
        agent_runtime_root=agent_runtime_root,
        runtime_root=runtime_root,
    )
    if not prelaunch_cleanup["success"]:
        return _fail_launch(
            "无法安全停止上一次 Agent Runtime 进程。",
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            status="cleanup_failed",
            prelaunch_cleanup=prelaunch_cleanup,
        )

    write_agent_runtime_debug_state(
        runtime_root,
        status="validating",
        message="正在校验 Agent Runtime 工程。",
    )
    pyproject_path = agent_runtime_root / "pyproject.toml"
    if agent_runtime_root.is_symlink():
        return _fail_launch(
            "未找到有效的 Agent Runtime 工程：agent-runtime/pyproject.toml。",
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            status="validation_failed",
            prelaunch_cleanup=prelaunch_cleanup,
        )
    if not pyproject_path.is_file():
        try:
            GitTemplatePackageBuilder(settings).materialize_missing_agent_runtime_root(root)
        except GitTemplateError as exc:
            return _fail_launch(
                str(exc) or "未找到有效的 Agent Runtime 工程：agent-runtime/pyproject.toml。",
                root=root,
                agent_runtime_root=agent_runtime_root,
                runtime_root=runtime_root,
                status="validation_failed",
                prelaunch_cleanup=prelaunch_cleanup,
            )
    if not pyproject_path.is_file():
        return _fail_launch(
            "未找到有效的 Agent Runtime 工程：agent-runtime/pyproject.toml。",
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            status="validation_failed",
            prelaunch_cleanup=prelaunch_cleanup,
        )
    uv_command = resolve_uv_command()
    if not uv_command:
        install_uv_with_official_script(runtime_root=runtime_root)
        uv_command = resolve_uv_command()
    if not uv_command:
        return _fail_launch(
            "未找到 Agent Runtime 包管理器命令：uv。",
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            status="validation_failed",
            prelaunch_cleanup=prelaunch_cleanup,
        )

    write_agent_runtime_debug_state(
        runtime_root,
        status="installing",
        message="正在同步 Agent Runtime 依赖。",
    )
    install = _run_agent_runtime_install(
        workspace=root,
        agent_runtime_root=agent_runtime_root,
        runtime_root=runtime_root,
        uv_command=uv_command,
    )
    if install["returncode"] != 0:
        return _fail_launch(
            "Agent Runtime 依赖同步失败。",
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            status="install_failed",
            install=install,
            prelaunch_cleanup=prelaunch_cleanup,
        )

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
    pid = process.pid if process is not None else None
    if process is None:
        return _fail_launch(
            "Agent Runtime 启动命令执行失败。",
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            status="start_failed",
            install=install,
            server=server_result,
            prelaunch_cleanup=prelaunch_cleanup,
            host="127.0.0.1",
            port=port,
        )
    register_agent_runtime_process(root, process)
    write_agent_runtime_debug_state(
        runtime_root,
        status="starting",
        message="正在启动 Agent Runtime。",
        pid=pid,
        host="127.0.0.1",
        port=port,
    )
    if process.poll() is not None:
        returncode = process.poll()
        return _fail_launch(
            f"Agent Runtime 进程已退出（退出码：{returncode}）。",
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            status="start_failed",
            install=install,
            server={**server_result, "returncode": returncode},
            prelaunch_cleanup=prelaunch_cleanup,
            host="127.0.0.1",
            port=port,
            process=process,
        )

    ready, health = _wait_for_agent_runtime_ready(runtime_url, process)
    returncode = process.poll()
    server = {
        **server_result,
        "ready": ready,
        "returncode": returncode,
        "ready_checked_at": datetime.now(UTC).isoformat(),
    }
    if not ready:
        if returncode is not None:
            return _fail_launch(
                f"Agent Runtime 进程已退出（退出码：{returncode}）。",
                root=root,
                agent_runtime_root=agent_runtime_root,
                runtime_root=runtime_root,
                status="start_failed",
                install=install,
                server=server,
                prelaunch_cleanup=prelaunch_cleanup,
                host="127.0.0.1",
                port=port,
                process=process,
            )
        server["cleanup"] = terminate_agent_runtime_process(
            workspace=root,
            process=process,
            pid_file=None,
        )
        mark_agent_runtime_debug_state_offline(
            runtime_root,
            message="Agent Runtime 健康检查超时。",
            health=health if health is not None else "ETIMEDOUT",
            clear_pid=True,
        )
        return _failed_agent_runtime_launch(
            "Agent Runtime 健康检查超时。",
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            failed_stage="agent_runtime_start",
            install=install,
            server=server,
            prelaunch_cleanup=prelaunch_cleanup,
        )

    try:
        write_agent_runtime_debug_state(
            runtime_root,
            status="running",
            message="Agent Runtime 已启动并就绪。",
            pid=pid,
            host="127.0.0.1",
            port=port,
            health=200,
            debug_token=gateway_token if include_debug_access else None,
        )
    except OSError as exc:
        server["cleanup"] = terminate_agent_runtime_process(
            workspace=root,
            process=process,
            pid_file=None,
        )
        return _fail_launch(
            f"Agent Runtime 已启动，但无法记录工作区调试状态：{exc}",
            root=root,
            agent_runtime_root=agent_runtime_root,
            runtime_root=runtime_root,
            status="debug_state_failed",
            install=install,
            server=server,
            prelaunch_cleanup=prelaunch_cleanup,
            host="127.0.0.1",
            port=port,
        )

    start_agent_runtime_heartbeat(root)
    result = {
        **_base_agent_runtime_payload(root, agent_runtime_root, runtime_root),
        "status": "running",
        "message": "Agent Runtime 已启动并就绪。",
        "install": install,
        "server": server,
        "prelaunch_cleanup": prelaunch_cleanup,
        "failed_stage": None,
        "_process": process,
    }
    if include_debug_access:
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
    runtime_root = Path(str(launch_result.get("runtime_root") or ""))
    stop_agent_runtime_heartbeat(root)
    server = launch_result.get("server")
    if not isinstance(server, dict):
        server = {}
        launch_result["server"] = server
    with agent_runtime_launch_lock(root):
        cleanup = terminate_agent_runtime_process(
            workspace=root,
            process=process,
            pid_file=None,
        )
        if runtime_root:
            mark_agent_runtime_debug_state_stopped(runtime_root)
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
    stop_agent_runtime_heartbeat(root)
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


def _fail_launch(
    message: str,
    *,
    root: Path,
    agent_runtime_root: Path,
    runtime_root: Path,
    status: str,
    install: dict[str, Any] | None = None,
    server: dict[str, Any] | None = None,
    prelaunch_cleanup: dict[str, Any] | None = None,
    host: str | None = None,
    port: int | None = None,
    process: subprocess.Popen[bytes] | None = None,
) -> dict[str, Any]:
    """写入融合失败 status，并返回不泄露 fallback 配置的启动失败结果。"""

    if process is not None:
        current_server = server if isinstance(server, dict) else {}
        current_server["cleanup"] = terminate_agent_runtime_process(
            workspace=root,
            process=process,
            pid_file=None,
        )
        server = current_server
    write_agent_runtime_debug_state(
        runtime_root,
        status=status,
        message=message,
        host=host,
        port=port,
        health=None,
        debug_token=None,
    )
    return _failed_agent_runtime_launch(
        message,
        root=root,
        agent_runtime_root=agent_runtime_root,
        runtime_root=runtime_root,
        failed_stage=failed_stage_for_status(status) or status,
        install=install,
        server=server,
        prelaunch_cleanup=prelaunch_cleanup,
    )


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
