from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.services.agent_runtime_project_launcher import (
    AgentRuntimeLaunchError,
    agent_runtime_launch_required,
    launch_agent_runtime_project,
    stop_agent_runtime_project,
    stop_workspace_agent_runtime_project,
)
from app.services.backend_project_launcher import (
    find_backend_project_root,
    launch_backend_project,
    stop_backend_project,
    stop_workspace_backend_project,
)
from app.services.frontend_project_launcher import (
    launch_frontend_project,
    stop_frontend_project,
)


LaunchProgressCallback = Callable[[str, str, str], None]


def launch_project_preview(
    workspace_path: str | Path,
    *,
    on_progress: LaunchProgressCallback | None = None,
) -> dict[str, Any]:
    """按工作区工程结构启动应用预览；数据源仅由实体设计决定。"""

    def report(stage: str, status: str, message: str) -> None:
        """把启动阶段进度转发给调用方，供节点流式展示真实耗时阶段。"""

        if on_progress is not None:
            on_progress(stage, status, message)

    root = Path(workspace_path).expanduser().resolve()
    report("structure", "running", "正在识别工程结构…")
    backend_project_root = find_backend_project_root(root)
    try:
        agent_runtime_required = agent_runtime_launch_required(root)
    except AgentRuntimeLaunchError as exc:
        report("structure", "failed", str(exc))
        return {
            "status": "failed",
            "message": str(exc),
            "workspace": str(root),
            "preview_url": None,
            "package_json_path": None,
            "server": None,
            "datasource_type": None,
            "backend": None,
            "agent_runtime": None,
            "frontend": None,
            "failed_stage": "agent_runtime_validation",
        }
    report("structure", "completed", "工程结构识别完成")
    backend_process = None
    if backend_project_root is None:
        backend = {
            "status": "skipped",
            "reason": "backend_project_missing",
            "message": "未识别到后端 Maven 工程，已跳过后端启动。",
            "workspace": str(root),
            "failed_stage": None,
        }
        report("backend", "skipped", backend["message"])
    else:
        report("backend", "running", "正在构建并启动后端服务，完成后进入前端启动…")
        backend = launch_backend_project(root)
        backend_process = backend.pop("_process", None)
        if backend.get("status") == "failed":
            report(
                "backend",
                "failed",
                str(backend.get("message") or "Java 后端启动失败。"),
            )
            return {
                "status": "failed",
                "message": str(backend.get("message") or "Java 后端启动失败。"),
                "workspace": str(root),
                "preview_url": None,
                "package_json_path": None,
                "server": None,
                "datasource_type": None,
                "backend": backend,
                "agent_runtime": None,
                "frontend": None,
                "failed_stage": backend.get("failed_stage") or "backend_start",
            }
        report(
            "backend",
            "completed",
            str(backend.get("message") or "后端服务已就绪。"),
        )

    agent_runtime_process = None
    if not agent_runtime_required:
        agent_runtime = {
            "status": "skipped",
            "reason": "agent_runtime_not_required",
            "message": "当前应用不包含业务 Agent，已跳过 Agent Runtime 启动。",
            "workspace": str(root),
            "failed_stage": None,
        }
    else:
        report("agent_runtime", "running", "正在启动 Agent Runtime 并等待健康检查…")
        agent_runtime = launch_agent_runtime_project(root)
        agent_runtime_process = agent_runtime.pop("_process", None)
        if agent_runtime.get("status") == "failed":
            if backend_process is not None:
                stop_backend_project(backend, backend_process)
            report(
                "agent_runtime",
                "failed",
                str(agent_runtime.get("message") or "Agent Runtime 启动失败。"),
            )
            return {
                "status": "failed",
                "message": str(
                    agent_runtime.get("message") or "Agent Runtime 启动失败。"
                ),
                "workspace": str(root),
                "preview_url": None,
                "package_json_path": None,
                "server": None,
                "datasource_type": None,
                "backend": backend,
                "agent_runtime": agent_runtime,
                "frontend": None,
                "failed_stage": (
                    agent_runtime.get("failed_stage") or "agent_runtime_start"
                ),
            }
        report(
            "agent_runtime",
            "completed",
            str(agent_runtime.get("message") or "Agent Runtime 已就绪。"),
        )

    report("frontend", "running", "正在启动前端服务并等待健康检查就绪…")
    frontend = launch_frontend_project(root)
    if frontend.get("status") == "failed":
        if agent_runtime_process is not None:
            stop_agent_runtime_project(agent_runtime, agent_runtime_process)
        if backend_process is not None:
            stop_backend_project(backend, backend_process)
        report(
            "frontend",
            "failed",
            str(frontend.get("message") or "前端启动失败。"),
        )
        return {
            **frontend,
            "status": "failed",
            "message": str(frontend.get("message") or "前端启动失败。"),
            "workspace": str(root),
            "datasource_type": None,
            "backend": backend,
            "agent_runtime": agent_runtime,
            "frontend": frontend,
            "failed_stage": "frontend_start",
        }

    report(
        "frontend",
        "completed",
        str(frontend.get("message") or "前端服务已就绪。"),
    )
    report("ready", "completed", "应用所需服务均已就绪，可以开始预览。")
    frontend_only = backend.get("status") == "skipped"
    return {
        **frontend,
        "status": "running",
        "message": (
            "前端项目已启动并就绪，未识别到后端工程。"
            if frontend_only and not agent_runtime_required
            else "Agent Runtime 与前端项目均已启动并就绪。"
            if frontend_only
            else "Java 后端、Agent Runtime 与前端项目均已启动并就绪。"
            if agent_runtime_required
            else "Java 后端与前端项目均已启动并就绪。"
        ),
        "workspace": str(root),
        "datasource_type": None,
        "backend": backend,
        "agent_runtime": agent_runtime,
        "frontend": frontend,
        "failed_stage": None,
    }


def stop_project_preview(workspace_path: str | Path) -> dict[str, Any]:
    """按前端、Runtime、Java 后端顺序停止工作区预览进程。"""

    root = Path(workspace_path).expanduser().resolve()
    frontend = stop_frontend_project(root)
    ui_design_frontend = stop_frontend_project(root, runtime_subdir="launch-ui-design")
    agent_runtime = stop_workspace_agent_runtime_project(root)
    backend = stop_workspace_backend_project(root)
    failed_parts = [
        name
        for name, result in (
            ("frontend", frontend),
            ("ui_design_frontend", ui_design_frontend),
            ("agent_runtime", agent_runtime),
            ("backend", backend),
        )
        if result.get("status") == "failed"
    ]
    return {
        "status": "failed" if failed_parts else "stopped",
        "message": (
            "部分预览服务停止失败：" + "、".join(failed_parts)
            if failed_parts
            else "工作区预览服务已停止。"
        ),
        "workspace": str(root),
        "frontend": frontend,
        "ui_design_frontend": ui_design_frontend,
        "agent_runtime": agent_runtime,
        "backend": backend,
    }


__all__ = [
    "find_backend_project_root",
    "launch_agent_runtime_project",
    "launch_backend_project",
    "launch_frontend_project",
    "launch_project_preview",
    "stop_backend_project",
    "stop_agent_runtime_project",
    "stop_frontend_project",
    "stop_project_preview",
    "stop_workspace_backend_project",
]
