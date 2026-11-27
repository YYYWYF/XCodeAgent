"""开发阶段调试入口的分端进程管理。"""

from pathlib import Path
from typing import Any

from app.services.backend_process_registry import backend_launch_lock
from app.services.backend_project_launcher import launch_backend_project, stop_workspace_backend_project
from app.services.frontend_project_launcher import launch_frontend_project, stop_frontend_project
from app.services.preview_diagnostics import diagnostic_from_exception
from app.services.preview_runtime_state import (
    PreviewLayer, begin_service_attempt, finish_service_attempt, mark_interrupted_attempt,
    read_record, record_progress, record_service_stop,
)
from app.services.project_launcher import LaunchProgressCallback, _application_has_no_backend_business


def restart_debug_service(
    workspace: str | Path, layer: PreviewLayer, *, on_progress: LaunchProgressCallback | None = None,
) -> dict[str, Any]:
    """只启动或重启目标服务，并沿用同应用的进程和构建锁。"""
    root = Path(workspace).expanduser().resolve()
    label = "前端" if layer == "frontend" else "后端"
    stop = stop_frontend_project if layer == "frontend" else stop_workspace_backend_project
    with backend_launch_lock(root):
        # 先停止旧进程再清空该端日志；停止失败时保留原进程记录供重试。
        stopped = stop(root)
        if stopped.get("status") == "failed":
            return {"status": "failed", "message": stopped["message"], "failed_stage": "stop", layer: stopped}
        attempt = begin_service_attempt(root, layer)

        def report(message: str) -> None:
            """实时更新当前服务，供右侧状态与日志订阅显示。"""
            record_progress(root, layer, "running", message)
            if on_progress:
                on_progress(layer, "running", message)

        try:
            report(f"正在启动{label}服务…")
            if layer == "frontend":
                # 旧进程已显式停止，前端启动器仍会按工程声明安装依赖和做就绪校验。
                result = launch_frontend_project(root)
            elif _application_has_no_backend_business(root):
                result = {"status": "skipped", "message": "当前应用无后端业务实体与 API 契约。"}
            else:
                result = launch_backend_project(root, on_progress=report)
                result.pop("_process", None)
        except Exception as exc:
            stage = f"{layer}_start"
            result = {"status": "failed", "message": str(exc), "failed_stage": stage,
                      "diagnostics": [diagnostic_from_exception(root, stage, exc)]}
        finish_service_attempt(root, layer, result)
        return {**result, "attempt_id": attempt, layer: result}


def recover_interrupted_debug_service(workspace: str, layer: PreviewLayer) -> None:
    """清理遗留的单端半启动进程；若启动已完成，保留已提交的结果。"""
    root = Path(workspace).resolve()
    stop = stop_frontend_project if layer == "frontend" else stop_workspace_backend_project
    with backend_launch_lock(root):
        if read_record(root)[layer].get("status") != "starting":
            return
        result = stop(root)
        if result.get("status") != "failed":
            record_service_stop(root, layer, result)
        mark_interrupted_attempt(root, layer=layer, scoped=True)
