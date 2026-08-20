from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

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
                "frontend": None,
                "failed_stage": backend.get("failed_stage") or "backend_start",
            }
        report(
            "backend",
            "completed",
            str(backend.get("message") or "后端服务已就绪。"),
        )

    report("frontend", "running", "正在启动前端服务并等待健康检查就绪…")
    frontend = launch_frontend_project(root)
    if frontend.get("status") == "failed":
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
            "frontend": frontend,
            "failed_stage": "frontend_start",
        }

    report(
        "frontend",
        "completed",
        str(frontend.get("message") or "前端服务已就绪。"),
    )
    report("ready", "completed", "前后端服务均已就绪，可以开始预览。")
    frontend_only = backend.get("status") == "skipped"
    return {
        **frontend,
        "status": "running",
        "message": (
            "前端项目已启动并就绪，未识别到后端工程。"
            if frontend_only
            else "Java 后端与前端项目均已启动并就绪。"
        ),
        "workspace": str(root),
        "datasource_type": None,
        "backend": backend,
        "frontend": frontend,
        "failed_stage": None,
    }


def stop_project_preview(workspace_path: str | Path) -> dict[str, Any]:
    """停止指定工作区已启动的前后端预览进程。"""

    root = Path(workspace_path).expanduser().resolve()
    frontend = stop_frontend_project(root)
    backend = stop_workspace_backend_project(root)
    failed_parts = [
        name
        for name, result in (("frontend", frontend), ("backend", backend))
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
        "backend": backend,
    }


__all__ = [
    "find_backend_project_root",
    "launch_backend_project",
    "launch_frontend_project",
    "launch_project_preview",
    "stop_backend_project",
    "stop_frontend_project",
    "stop_project_preview",
    "stop_workspace_backend_project",
]
