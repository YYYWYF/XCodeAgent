from __future__ import annotations

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
from app.services.data_source_policy import (
    DataSourcePolicyError,
    ensure_enabled_datasource_type,
    read_application_datasource_type,
)


def launch_project_preview(workspace_path: str | Path) -> dict[str, Any]:
    """按 application.json 权威数据源类型统一启动应用预览。"""

    root = Path(workspace_path).expanduser().resolve()
    try:
        datasource_type = ensure_enabled_datasource_type(
            read_application_datasource_type(root)
        )
    except DataSourcePolicyError as exc:
        return {
            "status": "failed",
            "message": str(exc),
            "workspace": str(root),
            "preview_url": None,
            "package_json_path": None,
            "server": None,
            "datasource_type": None,
            "backend": None,
            "frontend": None,
            "failed_stage": "datasource_policy",
        }

    backend_process = None
    if datasource_type == "static":
        # Static 的运行时数据全部位于前端内存，不能触发 Java 启动和数据库凭据解析。
        backend = {
            "status": "skipped",
            "reason": "static_frontend_only",
            "message": "Static 应用仅启动前端，已跳过 Java 后端。",
            "workspace": str(root),
            "failed_stage": None,
        }
    elif find_backend_project_root(root) is None:
        backend = {
            "status": "skipped",
            "reason": "backend_project_missing",
            "message": "未识别到后端 Maven 工程，已跳过后端启动。",
            "workspace": str(root),
            "failed_stage": None,
        }
    else:
        backend = launch_backend_project(root)
        backend_process = backend.pop("_process", None)
        if backend.get("status") == "failed":
            return {
                "status": "failed",
                "message": str(backend.get("message") or "Java 后端启动失败。"),
                "workspace": str(root),
                "preview_url": None,
                "package_json_path": None,
                "server": None,
                "datasource_type": datasource_type,
                "backend": backend,
                "frontend": None,
                "failed_stage": backend.get("failed_stage") or "backend_start",
            }

    frontend = launch_frontend_project(root)
    if frontend.get("status") == "failed":
        if backend_process is not None:
            stop_backend_project(backend, backend_process)
        return {
            **frontend,
            "status": "failed",
            "message": str(frontend.get("message") or "前端启动失败。"),
            "workspace": str(root),
            "datasource_type": datasource_type,
            "backend": backend,
            "frontend": frontend,
            "failed_stage": "frontend_start",
        }

    frontend_only = backend.get("status") == "skipped"
    return {
        **frontend,
        "status": "running",
        "message": (
            "Static 应用前端项目已启动并就绪。"
            if datasource_type == "static"
            else (
                "前端项目已启动并就绪，未识别到后端工程。"
                if frontend_only
                else "Java 后端与前端项目均已启动并就绪。"
            )
        ),
        "workspace": str(root),
        "datasource_type": datasource_type,
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
