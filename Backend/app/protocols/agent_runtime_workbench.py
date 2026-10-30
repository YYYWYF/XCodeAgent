"""Runtime 管理与调试沿独立 AG-UI 生命周期提供，不进入原 Workflow。"""

import asyncio
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.agent_runtime_workbench import runtime_agents, debug_snapshot, start_debug, stop_debug
from app.services.runtime_agent_workbench_settings import execute_settings
from app.services.runtime_agent_settings_requests import SETTINGS_ACTIONS, settings_request_adapter


class RuntimeWorkbenchInput(BaseModel):
    """严格限制 Runtime 动作，不接受客户端 Principal、模型配置或数据库凭据。"""

    model_config = ConfigDict(extra="forbid")
    action: Literal["list", "get_debug", "start_debug", "stop_debug"]
    workspaceRoot: str = Field(min_length=1, max_length=4096)
    agentId: str = Field(default="", max_length=128)
    origin: str = Field(default="", max_length=512)


def runtime_workbench_capabilities() -> dict:
    """发布当前 Runtime 独立产品协议及一次性凭据事件。"""

    return {"endpoint": "/agent-runtime-workbench/run", "transport": "ag-ui-sse",
            "actions": ["list", "get_debug", "start_debug", "stop_debug", *sorted(SETTINGS_ACTIONS)], "workflowIndependent": True,
            "customEventName": "agent-runtime-workbench", "stateSnapshotKey": "agentRuntimeWorkbench",
            "transientCredentialEvent": "agent-runtime-debug-credential"}


def build_runtime_workbench_stream(*, payload: dict, accept: str | None = None):
    """发出完整生命周期，凭据只在显式启动成功时发送临时 CUSTOM 事件。"""

    # 独立动作的正式草稿与公共生命周期必须绑定同一个 Run 身份。
    payload = {**payload, "runId": str(payload.get("runId") or f"runtime-workbench-{uuid4().hex[:12]}")}

    async def operation():
        """校验操作身份并串行进入 Runtime 服务，失败仍由公共 AG-UI 协议收口。"""

        raw = (payload.get("forwardedProps") or {}).get("agentRuntimeWorkbench") or {}
        thread_id = str(payload.get("threadId") or "")
        if not thread_id:
            raise ValueError("Runtime 工作台操作缺少 threadId。")
        if not isinstance(raw, dict):
            raise ValueError("Runtime 工作台操作必须是对象。")
        if raw.get("action") in SETTINGS_ACTIONS:
            request = settings_request_adapter.validate_python(raw)
            data = await asyncio.to_thread(execute_settings, request, thread_id=thread_id,
                                          run_id=payload["runId"])
            return AgUiActionResult(data={"action": request.action, **data},
                                    message="Agent 配置操作已完成。")
        request = RuntimeWorkbenchInput.model_validate(raw)
        data, transient = {}, ()
        if request.action == "list":
            data = await asyncio.to_thread(runtime_agents, request.workspaceRoot)
        elif request.action == "get_debug":
            data = {"debug": await asyncio.to_thread(debug_snapshot, request.workspaceRoot)}
        elif request.action == "stop_debug":
            data = {"debug": await asyncio.to_thread(stop_debug, request.workspaceRoot, thread_id)}
        else:
            worker = asyncio.create_task(asyncio.to_thread(start_debug, request.workspaceRoot, thread_id, request.origin, request.agentId))
            try:
                state, credential = await asyncio.shield(worker)
            except asyncio.CancelledError:
                # 取消不能让启动线程在后台遗留带凭据的进程；等其收口后只回收本会话。
                try:
                    await asyncio.shield(worker)
                finally:
                    await asyncio.to_thread(stop_debug, request.workspaceRoot, thread_id)
                raise
            data = {"debug": state}
            transient = (("agent-runtime-debug-credential", credential),)
        return AgUiActionResult(data={"action": request.action, **data}, message="Runtime 工作台操作已完成。", transient_events=transient)

    return build_ag_ui_action_stream(payload=payload, event_name="agent-runtime-workbench", state_key="agentRuntimeWorkbench",
                                    run_id_prefix="runtime-workbench", operation=operation,
                                    error_message_prefix="Runtime 操作失败", accept=accept)
