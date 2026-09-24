"""按拓扑蓝图声明的启动阶段顺序编排应用预览启动。

框架合同要求生成应用的启动顺序由拓扑决定，而不是由编排层硬编码：Direct 拓扑没有
Java Backend 阶段，并额外要求 integration_probe 证明前端能直连 Runtime 公开入口。
因此本模块把每个阶段实现为独立执行器，并严格按蓝图给出的序列执行；蓝图声明了未
实现的阶段时立即失败，不允许静默跳过。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any
from urllib.parse import urlparse

from app.services.agent_runtime_integration_probe import (
    probe_direct_runtime_integration,
)
from app.services.agent_runtime_project_launcher import (
    AgentRuntimeLaunchError,
    agent_runtime_launch_required,
    launch_agent_runtime_project,
    stop_agent_runtime_project,
)
from app.services.agent_runtime_uv import install_uv_with_official_script, resolve_uv_command
from app.services.application_config import read_application_config
from app.services.backend_project_launcher import (
    find_backend_project_root,
    launch_backend_project,
    stop_backend_project,
)
from app.services.frontend_project_launcher import launch_frontend_project
from app.topologies import (
    confirmed_launch_stages,
    read_confirmed_technical_plan,
    serves_agent_runtime_public_edge,
)

LaunchProgressCallback = Callable[[str, str, str], None]

# 尚未迁移到拓扑框架的当前流程顺序：Backend + Agent Runtime + Frontend。
# 仅在已确认 TechnicalPlan 没有拓扑投影时作为回退，迁移完成后可整体删除。
LEGACY_LAUNCH_STAGES = ("structure", "backend", "agent_runtime", "frontend", "ready")


@dataclass
class LaunchStageState:
    """保存启动阶段之间传递的进程句柄、阶段结果和真实服务地址。"""

    root: Path
    force_restart: bool
    technical_plan: dict[str, Any] | None
    runtime_public_edge: bool
    backend_project_root: Path | None = None
    agent_runtime_required: bool = False
    backend: dict[str, Any] | None = None
    backend_process: Any = None
    agent_runtime: dict[str, Any] | None = None
    agent_runtime_process: Any = None
    public_edge_url: str | None = None
    frontend: dict[str, Any] | None = None
    integration_probe: dict[str, Any] | None = None


def resolve_launch_stages(root: Path) -> tuple[str, ...]:
    """读取已确认拓扑声明的启动阶段顺序；未迁移流程回退到当前固定顺序。"""

    technical_plan = read_confirmed_technical_plan(root)
    if technical_plan is None:
        return LEGACY_LAUNCH_STAGES
    stages = confirmed_launch_stages(technical_plan, read_application_config(root))
    return stages or LEGACY_LAUNCH_STAGES


def run_launch_stages(
    state: LaunchStageState,
    stages: tuple[str, ...],
    report: LaunchProgressCallback,
) -> dict[str, Any]:
    """严格按阶段序列执行；遇到失败结果或未知阶段时立即停止。"""

    for stage in stages:
        executor = _STAGE_EXECUTORS.get(stage)
        if executor is None:
            raise ValueError(f"拓扑声明了未实现的启动阶段：{stage}。")
        failure = executor(state, report)
        if failure is not None:
            return failure
    return _successful_result(state)


def _run_structure_stage(
    state: LaunchStageState, report: LaunchProgressCallback
) -> dict[str, Any] | None:
    """识别工程结构，并确认当前工作区是否需要 Agent Runtime。"""

    report("structure", "running", "正在识别工程结构…")
    state.backend_project_root = find_backend_project_root(state.root)
    try:
        state.agent_runtime_required = agent_runtime_launch_required(state.root)
    except AgentRuntimeLaunchError as exc:
        report("structure", "failed", str(exc))
        return _launch_failure(
            state, message=str(exc), failed_stage="agent_runtime_validation"
        )
    report("structure", "completed", "工程结构识别完成")
    return None


def _run_backend_stage(
    state: LaunchStageState, report: LaunchProgressCallback
) -> dict[str, Any] | None:
    """构建并启动 Java 后端；未识别到后端工程时记录跳过证据。"""

    if state.backend_project_root is None:
        state.backend = {
            "status": "skipped",
            "reason": "backend_project_missing",
            "message": "未识别到后端 Maven 工程，已跳过后端启动。",
            "workspace": str(state.root),
            "failed_stage": None,
        }
        report("backend", "skipped", state.backend["message"])
        return None
    report("backend", "running", "正在构建并启动后端服务，完成后进入前端启动…")
    backend = launch_backend_project(state.root)
    state.backend_process = backend.pop("_process", None)
    state.backend = backend
    if backend.get("status") == "failed":
        message = str(backend.get("message") or "Java 后端启动失败。")
        report("backend", "failed", message)
        return _launch_failure(
            state,
            message=message,
            failed_stage=str(backend.get("failed_stage") or "backend_start"),
        )
    report("backend", "completed", str(backend.get("message") or "后端服务已就绪。"))
    return None


def _run_agent_runtime_stage(
    state: LaunchStageState, report: LaunchProgressCallback
) -> dict[str, Any] | None:
    """启动 Agent Runtime，并把本轮真实公开入口地址交给后续阶段。"""

    if not state.agent_runtime_required:
        state.agent_runtime = {
            "status": "skipped",
            "reason": "agent_runtime_not_required",
            "message": "当前应用不包含业务 Agent，已跳过 Agent Runtime 启动。",
            "workspace": str(state.root),
            "failed_stage": None,
        }
        return None
    report("agent_runtime", "running", "正在启动 Agent Runtime 并等待健康检查…")
    agent_runtime = launch_agent_runtime_project(state.root)
    state.agent_runtime_process = agent_runtime.pop("_process", None)
    state.public_edge_url = agent_runtime.pop("_public_edge_url", None)
    state.agent_runtime = agent_runtime
    if agent_runtime.get("status") == "failed":
        _stop_started_processes(state)
        message = str(agent_runtime.get("message") or "Agent Runtime 启动失败。")
        report("agent_runtime", "failed", message)
        return _launch_failure(
            state,
            message=message,
            failed_stage=str(agent_runtime.get("failed_stage") or "agent_runtime_start"),
        )
    report(
        "agent_runtime",
        "completed",
        str(agent_runtime.get("message") or "Agent Runtime 已就绪。"),
    )
    return None


def _run_database_migration_stage(
    state: LaunchStageState, report: LaunchProgressCallback
) -> dict[str, Any] | None:
    """在 Runtime 启动前执行 Direct 的已生成业务迁移，失败即阻断预览。"""

    plan = state.technical_plan if isinstance(state.technical_plan, dict) else {}
    entities = [item for item in plan.get("entities", []) if isinstance(item, dict)]
    if not entities:
        report("database_migration", "skipped", "当前应用无业务 Entity，已跳过业务迁移。")
        return None
    runtime_root = state.root / "agent-runtime"
    runner_path = runtime_root / "src/app/infrastructure/migrations/runner.py"
    sql_root = runtime_root / "src/app/infrastructure/migrations/sql"
    if not runner_path.is_file() or not sql_root.is_dir() or not list(sql_root.glob("*.sql")):
        message = "Direct 业务 Entity 缺少迁移执行器或已生成的 SQL 文件。"
        report("database_migration", "failed", message)
        return _launch_failure(state, message=message, failed_stage="database_migration")
    uv_command = resolve_uv_command()
    if not uv_command:
        install_uv_with_official_script(runtime_root=state.root / ".xcodeagent/runtime/launch")
        uv_command = resolve_uv_command()
    if not uv_command:
        message = "业务迁移需要 uv，但当前未找到可用命令。"
        report("database_migration", "failed", message)
        return _launch_failure(state, message=message, failed_stage="database_migration")
    report("database_migration", "running", "正在执行 Direct 业务数据库迁移…")
    try:
        result = subprocess.run(
            [str(uv_command), "run", "--project", str(runtime_root), "python", "-m",
             "app.infrastructure.migrations.runner"],
            cwd=runtime_root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        message = f"业务迁移执行失败：{type(exc).__name__}。"
        report("database_migration", "failed", message)
        return _launch_failure(state, message=message, failed_stage="database_migration")
    if result.returncode != 0:
        message = "业务迁移失败：" + (result.stderr.strip() or result.stdout.strip())[-1500:]
        report("database_migration", "failed", message)
        return _launch_failure(state, message=message, failed_stage="database_migration")
    report("database_migration", "completed", "Direct 业务数据库迁移已完成。")
    return None


def _run_frontend_stage(
    state: LaunchStageState, report: LaunchProgressCallback
) -> dict[str, Any] | None:
    """启动前端服务；公开入口由 Runtime 承担时注入本轮真实地址。"""

    report("frontend", "running", "正在启动前端服务并等待健康检查就绪…")
    # 公开入口由 Runtime 自身承担时，把本轮真实地址注入前端构建期变量。
    frontend_environment = (
        {
            "VITE_AGENT_RUNTIME_BASE_URL": str(state.public_edge_url),
            "VITE_BACKEND_BASE_URL": str(state.public_edge_url),
        }
        if state.agent_runtime_required
        and state.runtime_public_edge
        and state.public_edge_url
        else None
    )
    launch_kwargs = (
        {"environment_overrides": frontend_environment}
        if frontend_environment is not None
        else {}
    )
    frontend = (
        launch_frontend_project(state.root, force_restart=True, **launch_kwargs)
        if state.force_restart
        else launch_frontend_project(state.root, **launch_kwargs)
    )
    state.frontend = frontend
    if frontend.get("status") == "failed":
        _stop_started_processes(state)
        message = str(frontend.get("message") or "前端启动失败。")
        report("frontend", "failed", message)
        # 前端启动结果与预览结果同构，失败时平铺返回以保留其诊断字段。
        return {
            **frontend,
            "status": "failed",
            "message": message,
            "workspace": str(state.root),
            "datasource_type": None,
            "backend": state.backend,
            "agent_runtime": state.agent_runtime,
            "frontend": frontend,
            "failed_stage": "frontend_start",
        }
    report("frontend", "completed", str(frontend.get("message") or "前端服务已就绪。"))
    return None


def _run_integration_probe_stage(
    state: LaunchStageState, report: LaunchProgressCallback
) -> dict[str, Any] | None:
    """证明前端可用注入地址直连 Runtime 公开入口，否则阻断就绪。"""

    report("integration_probe", "running", "正在验证前端直连 Agent Runtime 公开入口…")
    if not state.public_edge_url:
        message = "拓扑要求执行集成探针，但本轮未产生 Agent Runtime 公开入口地址。"
        report("integration_probe", "failed", message)
        _stop_started_processes(state)
        return _launch_failure(state, message=message, failed_stage="integration_probe")
    probe = probe_direct_runtime_integration(
        runtime_url=state.public_edge_url,
        frontend_origin=_frontend_origin(state.frontend),
        technical_plan=state.technical_plan,
    )
    state.integration_probe = probe
    if not probe["passed"]:
        message = f"前端直连 Agent Runtime 公开入口验证失败：{probe['message']}"
        report("integration_probe", "failed", message)
        _stop_started_processes(state)
        return _launch_failure(state, message=message, failed_stage="integration_probe")
    report("integration_probe", "completed", str(probe["message"]))
    return None


def _run_ready_stage(
    state: LaunchStageState, report: LaunchProgressCallback
) -> dict[str, Any] | None:
    """在全部阶段通过后宣告预览所需服务均已就绪。"""

    del state
    report("ready", "completed", "应用所需服务均已就绪，可以开始预览。")
    return None


_STAGE_EXECUTORS: dict[
    str, Callable[[LaunchStageState, LaunchProgressCallback], dict[str, Any] | None]
] = {
    "structure": _run_structure_stage,
    "backend": _run_backend_stage,
    "database_migration": _run_database_migration_stage,
    "agent_runtime": _run_agent_runtime_stage,
    "frontend": _run_frontend_stage,
    "integration_probe": _run_integration_probe_stage,
    "ready": _run_ready_stage,
}


def _stop_started_processes(state: LaunchStageState) -> None:
    """停止本轮已启动的服务，避免失败后残留半套进程。"""

    if state.agent_runtime is not None and state.agent_runtime_process is not None:
        stop_agent_runtime_project(state.agent_runtime, state.agent_runtime_process)
        state.agent_runtime_process = None
    if state.backend is not None and state.backend_process is not None:
        stop_backend_project(state.backend, state.backend_process)
        state.backend_process = None


def _frontend_origin(frontend: dict[str, Any] | None) -> str | None:
    """从已启动前端结果读取浏览器访问源，用于复现真实 CORS 预检。"""

    if not isinstance(frontend, dict):
        return None
    parsed = urlparse(str(frontend.get("preview_url") or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _successful_result(state: LaunchStageState) -> dict[str, Any]:
    """把各阶段产出的真实结果合成为预览启动结果。"""

    frontend = state.frontend or {}
    no_backend = (
        not isinstance(state.backend, dict) or state.backend.get("status") == "skipped"
    )
    if no_backend and not state.agent_runtime_required:
        message = "前端项目已启动并就绪，未识别到后端工程。"
    elif no_backend:
        message = "Agent Runtime 与前端项目均已启动并就绪。"
    elif state.agent_runtime_required:
        message = "Java 后端、Agent Runtime 与前端项目均已启动并就绪。"
    else:
        message = "Java 后端与前端项目均已启动并就绪。"
    result: dict[str, Any] = {
        **frontend,
        "status": "running",
        "message": message,
        "workspace": str(state.root),
        "datasource_type": None,
        "backend": state.backend,
        "agent_runtime": state.agent_runtime,
        "frontend": frontend,
        "failed_stage": None,
    }
    if state.integration_probe is not None:
        result["integration_probe"] = state.integration_probe
    return result


def _launch_failure(
    state: LaunchStageState, *, message: str, failed_stage: str
) -> dict[str, Any]:
    """构造与既有预览失败契约一致的失败结果。"""

    return {
        "status": "failed",
        "message": message,
        "workspace": str(state.root),
        "preview_url": None,
        "package_json_path": None,
        "server": None,
        "datasource_type": None,
        "backend": state.backend,
        "agent_runtime": state.agent_runtime,
        "frontend": state.frontend,
        "failed_stage": failed_stage,
    }


__all__ = [
    "LEGACY_LAUNCH_STAGES",
    "LaunchProgressCallback",
    "LaunchStageState",
    "resolve_launch_stages",
    "run_launch_stages",
]
