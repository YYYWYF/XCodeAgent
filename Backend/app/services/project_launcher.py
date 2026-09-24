from __future__ import annotations

import os
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
from app.topologies import read_confirmed_technical_plan, serves_agent_runtime_public_edge


LaunchProgressCallback = Callable[[str, str, str], None]
LaunchValidationProgressCallback = Callable[[dict[str, Any]], None]


def launch_project_preview(
    workspace_path: str | Path,
    *,
    on_progress: LaunchProgressCallback | None = None,
    force_restart: bool = False,
) -> dict[str, Any]:
    """串行维护标准预览并同步所有入口的运行事实。"""
    from app.services.backend_process_registry import backend_launch_lock
    from app.services.preview_runtime_state import begin_attempt, finish_attempt, record_progress

    root = Path(workspace_path).expanduser().resolve()
    with backend_launch_lock(root):
        # 在清空本轮日志前停止旧进程，避免旧输出污染新的诊断证据。
        if force_restart:
            frontend_stop = stop_frontend_project(root)
            backend_stop = stop_workspace_backend_project(root)
            if any(part.get("status") == "failed" for part in (frontend_stop, backend_stop)):
                result = {"status": "failed", "message": "旧服务停止失败，未启动新的服务。", "failed_stage": "stop", "frontend": frontend_stop, "backend": backend_stop}
                finish_attempt(root, result)
                return result
        begin_attempt(root)

        def report(stage: str, status: str, message: str) -> None:
            """同时向公共状态和调用者推送阶段变化。"""
            record_progress(root, stage, status, message)
            if on_progress:
                on_progress(stage, status, message)

        try:
            result = _launch_project_preview(root, on_progress=report, force_restart=force_restart)
        except Exception as exc:
            result = {"status": "failed", "message": str(exc), "failed_stage": "launch"}
        finish_attempt(root, result)
        return result


def _launch_project_preview(
    workspace_path: str | Path,
    *,
    on_progress: LaunchProgressCallback | None = None,
    force_restart: bool = False,
) -> dict[str, Any]:
    """按已确认拓扑声明的阶段序列启动应用预览。

    启动顺序由拓扑蓝图决定，本模块不再自行推断服务结构，也不保留第二套内联
    启动实现：没有已确认拓扑时 ``resolve_launch_stages`` 内部会回退到当前固定
    顺序，因此这里无条件委派给阶段链。
    """

    def report(stage: str, status: str, message: str) -> None:
        """把启动阶段进度转发给调用方，供节点流式展示真实耗时阶段。"""

        if on_progress is not None:
            on_progress(stage, status, message)

    from app.services.project_launch_stages import (
        LaunchStageState,
        resolve_launch_stages,
        run_launch_stages,
    )

    root = Path(workspace_path).expanduser().resolve()
    technical_plan = read_confirmed_technical_plan(root)
    # 公开入口由 Runtime 自身承担时，前端需要用本轮真实 Runtime 地址构建。
    runtime_public_edge = (
        technical_plan is not None
        and serves_agent_runtime_public_edge(technical_plan)
    )
    return run_launch_stages(
        LaunchStageState(
            root=root,
            force_restart=force_restart,
            technical_plan=technical_plan,
            runtime_public_edge=runtime_public_edge,
        ),
        resolve_launch_stages(root),
        report,
    )


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
    result = {
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
    from app.services.preview_runtime_state import finish_attempt

    finish_attempt(root, result)
    return result


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
    result = {
        "status": "failed" if failed_parts else "stopped",
        "message": "部分标准预览服务停止失败：" + "、".join(failed_parts) if failed_parts else "标准项目预览已停止。",
        "workspace": str(root),
        "frontend": frontend,
        "backend": backend,
    }
    from app.services.preview_runtime_state import finish_attempt

    finish_attempt(root, result)
    return result


def inspect_project_preview(workspace_path: str | Path) -> dict[str, Any]:
    """只读取 standard preview 与 Agent Runtime 存活状态，不启动、停止或修改工作区。"""

    from app.services.agent_runtime_debug_state import read_agent_runtime_debug_pid

    root = Path(workspace_path).expanduser().resolve()
    runtime_root = root / ".xcodeagent" / "runtime" / "launch"
    backend = {"running": _pid_file_is_running(runtime_root / "backend.pid")}
    frontend = {"running": _pid_file_is_running(runtime_root / "frontend.pid")}
    agent_runtime_pid = read_agent_runtime_debug_pid(runtime_root)
    agent_runtime = {
        "running": _process_pid_is_running(agent_runtime_pid),
    }
    return {
        "workspace": str(root),
        "backend": backend,
        "frontend": frontend,
        "agent_runtime": agent_runtime,
        "running": backend["running"] or frontend["running"] or agent_runtime["running"],
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
    except (OSError, ValueError):
        return False
    return _process_pid_is_running(pid)


def _process_pid_is_running(pid: int | None) -> bool:
    """以零信号确认指定 PID 仍存活。"""

    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


__all__ = [
    "find_backend_project_root",
    "launch_agent_runtime_project",
    "launch_backend_project",
    "launch_frontend_project",
    "launch_project_preview",
    "run_project_restart_validation",
    "inspect_project_preview",
    "stop_backend_project",
    "stop_agent_runtime_project",
    "stop_frontend_project",
    "stop_project_preview",
    "stop_standard_project_preview",
    "stop_workspace_backend_project",
]
