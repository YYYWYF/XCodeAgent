from __future__ import annotations

import json
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


LaunchProgressCallback = Callable[[str, str, str], None]
LaunchValidationProgressCallback = Callable[[dict[str, Any]], None]


def _empty_list(value: Any) -> bool:
    """判断是否为"存在且为空"的列表；字段缺失或类型不符都不算空。"""

    return isinstance(value, list) and len(value) == 0


def _plan_declares_no_backend_business(root: Path) -> bool | None:
    """从 TechnicalPlan 判定是否无后端业务；计划文件不可用时返回 None。"""

    plan_path = root / ".xcodeagent" / "plans" / "technical-plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(plan, dict):
        return None
    return _empty_list(plan.get("entities")) and _empty_list(plan.get("api_contracts"))


def _lifecycle_declares_no_backend_business(root: Path) -> bool | None:
    """从 lifecycle 的已确认产物判定是否无后端业务；状态文件不可用时返回 None。"""

    lifecycle_path = root / ".xcodeagent" / "application-lifecycle.json"
    try:
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(lifecycle, dict):
        return None
    artifacts = lifecycle.get("developmentArtifacts")
    if not isinstance(artifacts, dict):
        return None
    # 产物进度按对象 id 建字典：没有任何实体与接口即纯前端运行时。
    entities = artifacts.get("entities")
    endpoints = artifacts.get("endpoints")
    if not isinstance(entities, dict) or not isinstance(endpoints, dict):
        return None
    return not entities and not endpoints


def _application_has_no_backend_business(root: Path) -> bool:
    """工作区无后端业务实体且无 API 契约时，视为纯前端运行时。

    与 integration_test_runner._application_has_no_backend_business 同样的判定，
    但直接读工作区状态文件，因为启动入口只有 workspace_path 而无 Graph state。

    计划文件优先，缺失时回退读 lifecycle：发起新迭代会清空 `.xcodeagent/plans`，
    此时"计划文件不存在"只说明还没规划，**不等于"需要后端"**——按后者处理会让纯前端
    应用白白拉起一个后端（慢、占端口，且本机没有 java 时直接启动失败、连预览都看不到）。
    两处状态都读不到时才保守认为需要后端。
    """

    declared = _plan_declares_no_backend_business(root)
    if declared is not None:
        return declared
    return _lifecycle_declares_no_backend_business(root) is True


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
    """按工作区工程结构启动应用预览；强制模式会重启 standard preview。"""

    def report(stage: str, status: str, message: str) -> None:
        """把启动阶段进度转发给调用方，供节点流式展示真实耗时阶段。"""

        if on_progress is not None:
            on_progress(stage, status, message)

    root = Path(workspace_path).expanduser().resolve()
    report("structure", "running", "正在识别工程结构…")
    backend_project_root = find_backend_project_root(root)
    no_backend_business = _application_has_no_backend_business(root)
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
    if backend_project_root is None or no_backend_business:
        backend = {
            "status": "skipped",
            "reason": (
                "no_backend_business"
                if no_backend_business
                else "backend_project_missing"
            ),
            "message": (
                "应用无后端业务实体与 API 契约，跳过后端启动。"
                if no_backend_business
                else "未识别到后端 Maven 工程，已跳过后端启动。"
            ),
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
    frontend = (
        launch_frontend_project(root, force_restart=True)
        if force_restart
        else launch_frontend_project(root)
    )
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
