"""工作区 Agent Runtime 独立调试启动的 AG-UI 协议适配。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.protocols.ag_ui_action_stream import (
    AgUiActionProgress,
    AgUiActionResult,
    ProgressReporter,
    build_ag_ui_action_stream,
)
from app.services.agent_runtime_project_launcher import launch_agent_runtime_project


AGENT_RUNTIME_DEBUG_EVENT_NAME = "agent-runtime-debug"


class AgentRuntimeDebugRequest(BaseModel):
    """校验独立启动动作和受管工作区路径。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    action: Literal["start"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)


class AgentRuntimeDebugError(RuntimeError):
    """保存可安全返回前端的 Runtime 启动失败阶段。"""

    def __init__(self, message: str, *, failed_stage: str | None = None) -> None:
        """初始化不包含密钥、环境变量或内部地址的调试错误。"""

        super().__init__(message)
        self.failed_stage = failed_stage


def agent_runtime_debug_capabilities() -> dict[str, Any]:
    """发布独立 Runtime 调试动作的 AG-UI 元数据。"""

    return {
        "name": "agent-runtime-debug",
        "endpoint": "/agent-runtime-debug/run",
        "transport": "ag-ui-sse",
        "actionField": "forwardedProps.agentRuntimeDebug",
        "actions": ["start"],
        "customEventName": AGENT_RUNTIME_DEBUG_EVENT_NAME,
        "stateSnapshotKey": "agentRuntimeDebug",
        "workflowIndependent": True,
    }


def agent_runtime_debug_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    """从 AG-UI forwardedProps 中提取 Runtime 调试参数。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return None
    value = forwarded_props.get("agentRuntimeDebug")
    return value if isinstance(value, dict) else None


def build_agent_runtime_debug_ag_ui_stream(
    *,
    payload: dict[str, Any],
    accept: str | None = None,
) -> AsyncIterator[str]:
    """独立启动工作区 Runtime，并发送完整 AG-UI 生命周期。"""

    raw_request = agent_runtime_debug_input(payload)
    if raw_request is None:
        raise ValueError("缺少 forwardedProps.agentRuntimeDebug。")
    workspace_root = str(raw_request.get("workspaceRoot") or "").strip()

    async def operation(report: ProgressReporter) -> AgUiActionResult:
        """在线程池运行阻塞启动器，并只返回无敏感调试证据。"""

        request = AgentRuntimeDebugRequest.model_validate(raw_request)
        workspace = _validated_debug_workspace(request.workspace_root)
        await report(
            AgUiActionProgress(
                stage="starting",
                message="正在同步依赖并启动 Agent Runtime…",
                percent=20,
                data={"action": request.action},
            )
        )
        launch_result = await asyncio.to_thread(
            launch_agent_runtime_project,
            workspace,
            include_debug_access=True,
        )
        launch_result.pop("_process", None)
        if launch_result.get("status") != "running":
            raise AgentRuntimeDebugError(
                _public_launch_error_message(launch_result),
                failed_stage=str(launch_result.get("failed_stage") or "") or None,
            )
        return AgUiActionResult(
            data={
                "action": request.action,
                "runtime": _public_runtime_result(launch_result),
            },
            message="Agent Runtime 已启动并通过健康检查。",
        )

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=AGENT_RUNTIME_DEBUG_EVENT_NAME,
        state_key="agentRuntimeDebug",
        run_id_prefix="agent-runtime-debug",
        progress_operation=operation,
        error_message_prefix="Agent Runtime 调试启动失败",
        error_data=lambda exc: {
            "action": raw_request.get("action"),
            "runtime": {
                "status": "failed",
                "failedStage": getattr(exc, "failed_stage", None),
            },
        },
        accept=accept,
        workspace_root=workspace_root or None,
    )


def _validated_debug_workspace(workspace_root: str) -> Path:
    """仅允许真实、非符号链接且由 XCodeAgent 管理的工作区。"""

    candidate = Path(workspace_root).expanduser()
    if candidate.is_symlink():
        raise ValueError("不能通过符号链接启动 Agent Runtime。")
    workspace = candidate.resolve(strict=False)
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError("应用工作区不存在或不是普通目录。")
    marker = workspace / ".xcodeagent" / "application.json"
    if not marker.is_file() or marker.is_symlink():
        raise ValueError("该目录不是由 XCodeAgent 管理的应用工作区。")
    return workspace


def _public_runtime_result(launch_result: dict[str, Any]) -> dict[str, Any]:
    """只向显式调试动作返回健康状态和本次 Runtime 临时访问凭据。"""

    server = launch_result.get("server")
    server = server if isinstance(server, dict) else {}
    debug_access = launch_result.pop("_debug_access", None)
    debug_access = debug_access if isinstance(debug_access, dict) else {}
    return {
        "status": "running",
        "message": str(launch_result.get("message") or "Agent Runtime 已启动。"),
        "ready": server.get("ready") is True,
        "pid": server.get("pid") if isinstance(server.get("pid"), int) else None,
        "managedModelFallbackAvailable": True,
        "runtimeUrl": str(debug_access.get("runtime_url") or ""),
        "debugGatewayToken": str(debug_access.get("gateway_token") or ""),
    }


def _public_launch_error_message(launch_result: dict[str, Any]) -> str:
    """为调试按钮拼接经过长度和换行裁剪的安全清理失败原因。"""

    message = str(launch_result.get("message") or "Agent Runtime 启动失败。")
    if launch_result.get("failed_stage") != "agent_runtime_cleanup":
        return message
    cleanup = launch_result.get("prelaunch_cleanup")
    detail = cleanup.get("error") if isinstance(cleanup, dict) else None
    if not isinstance(detail, str) or not detail.strip():
        return message
    sanitized_detail = " ".join(detail.split())[:500]
    return f"{message} {sanitized_detail}"
