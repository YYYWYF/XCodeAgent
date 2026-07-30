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
    "stop_backend_project",
    "stop_frontend_project",
    "stop_project_preview",
    "stop_workspace_backend_project",
]
