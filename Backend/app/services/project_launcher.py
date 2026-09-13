from __future__ import annotations

import os
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
LaunchValidationProgressCallback = Callable[[dict[str, Any]], None]


def launch_project_preview(
    workspace_path: str | Path,
    *,
    on_progress: LaunchProgressCallback | None = None,
    force_restart: bool = False,
) -> dict[str, Any]:
    """按工作区工程结构启动应用预览；强制模式会重启 standard preview。"""

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
    frontend = (
        launch_frontend_project(root, force_restart=True)
        if force_restart
        else launch_frontend_project(root)
    )
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
    ui_design_frontend = stop_frontend_project(root, runtime_subdir="launch-ui-design")
    backend = stop_workspace_backend_project(root)
    failed_parts = [
        name
        for name, result in (
            ("frontend", frontend),
            ("ui_design_frontend", ui_design_frontend),
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
        "backend": backend,
    }


def stop_standard_project_preview(workspace_path: str | Path) -> dict[str, Any]:
    """只停止标准项目预览，避免模板验收影响 UI Design 等独立 Runtime。"""

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
        "message": "部分标准预览服务停止失败：" + "、".join(failed_parts) if failed_parts else "标准项目预览已停止。",
        "workspace": str(root),
        "frontend": frontend,
        "backend": backend,
    }


def inspect_project_preview(workspace_path: str | Path) -> dict[str, Any]:
    """只读取 standard preview PID 存活状态，不启动、停止或修改工作区。"""

    root = Path(workspace_path).expanduser().resolve()
    runtime_root = root / ".xcodeagent" / "runtime" / "launch"
    backend = {"running": _pid_file_is_running(runtime_root / "backend.pid")}
    frontend = {"running": _pid_file_is_running(runtime_root / "frontend.pid")}
    return {
        "workspace": str(root),
        "backend": backend,
        "frontend": frontend,
        "running": backend["running"] or frontend["running"],
    }


def run_project_restart_validation(
    state: dict[str, Any],
    *,
    on_progress: LaunchValidationProgressCallback | None = None,
) -> dict[str, Any]:
    """将真实工程强制重启适配为既有质量门使用的结构化启动验收结果。"""

    workspace = str(state.get("workspace") or state.get("workspace_path") or "").strip()
    if not workspace:
        check = _project_restart_check(
            passed=False,
            message="缺少当前 Workspace，无法执行项目重启验收。",
            code="PROJECT_LAUNCH_FAILED",
            layers=["workspace"],
        )
        return {"test_results": [check], "test_events": [check["id"]]}

    def report(stage: str, status: str, message: str) -> None:
        """将 Launcher 进度转发成测试和审查子图可消费的检查事件。"""

        if on_progress is not None:
            on_progress({
                "status": "passed" if status == "completed" else "failed" if status == "failed" else "running",
                "check": {
                    "id": "project_restart",
                    "name": "项目重启验收",
                    "layer": "workspace",
                    "evidence": f"{stage}/{status}：{message}",
                },
            })

    launch = launch_project_preview(workspace, force_restart=True, on_progress=report)
    layers = _validation_layers(state)
    checks = [
        _project_restart_check(
            passed=launch.get("status") == "running",
            message=str(launch.get("message") or "项目重启验收失败。"),
            code=None if launch.get("status") == "running" else "PROJECT_LAUNCH_FAILED",
            layers=[layer],
            launch=launch,
        )
        for layer in layers
    ]
    return {"test_results": checks, "test_events": [check["id"] for check in checks]}


def _validation_layers(state: dict[str, Any]) -> list[str]:
    """从当前 ChangeSet 推导展示层；无法归类时保留 workspace 级验收。"""

    layers: list[str] = []
    for key in ("code_changes", "code_change_sets", "direct_code_change_sets", "small_task_code_change_sets"):
        value = state.get(key)
        change_sets = [value] if isinstance(value, dict) else value if isinstance(value, list) else []
        for change_set in change_sets:
            if not isinstance(change_set, dict):
                continue
            for item in change_set.get("files", []):
                path = str(item.get("path") or "").replace("\\", "/").lstrip("/") if isinstance(item, dict) else ""
                layer = path.split("/", 1)[0].casefold()
                if layer in {"frontend", "backend"} and layer not in layers:
                    layers.append(layer)
    return layers or ["workspace"]


def _project_restart_check(*, passed: bool, message: str, code: str | None, layers: list[str], launch: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造与既有质量门兼容的单层真实项目重启验收结果。"""

    layer = layers[0]
    return {
        "id": f"{layer}_project_restart",
        "name": f"{layer} 项目重启验收",
        "layer": layer,
        "passed": passed,
        "required": True,
        "blocking": True,
        "repairable": False,
        "code": code,
        "evidence": message,
        "startup": {"launch": launch or {}, "code": code},
    }


def _pid_file_is_running(path: Path) -> bool:
    """读取启动器 PID 文件并以零信号确认进程仍存活。"""

    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


__all__ = [
    "find_backend_project_root",
    "launch_backend_project",
    "launch_frontend_project",
    "launch_project_preview",
    "run_project_restart_validation",
    "inspect_project_preview",
    "stop_backend_project",
    "stop_frontend_project",
    "stop_project_preview",
    "stop_standard_project_preview",
    "stop_workspace_backend_project",
]
