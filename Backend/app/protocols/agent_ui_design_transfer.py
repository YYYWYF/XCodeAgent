"""已确认设计到正式目标的独立 AG-UI 计划、受控执行与查询。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Literal
from contextlib import suppress

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.protocols.ag_ui_action_stream import AgUiActionProgress, AgUiActionResult, ProgressReporter, build_ag_ui_action_stream
from app.services.agent_ui_design_transfer_plan import prepare_transfer_plan
from app.services.agent_ui_design_transfer import execute_transfer, process_run_id
from app.services.agent_ui_transfer_records import read_transfer, select_installation
from app.services.agent_ui_dependency_install import target_lock
from app.services.workspace_process_registry import workspace_process_registry


class AgentUiTransferAction(BaseModel):
    """仅接受当前工作区和安装身份，不接受调用者提供源码、diff 或执行命令。"""

    model_config = ConfigDict(extra="forbid")
    workspace: str = Field(min_length=1, max_length=4096)
    action: Literal["plan", "initialize", "confirm", "get", "cancel"]
    installationPlanId: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")
    planId: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")
    targetSourceRoot: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def require_plan(self) -> AgentUiTransferAction:
        """非规划动作必须绑定服务端具体计划，执行还需原安装身份。"""
        if self.action != "plan" and not self.planId:
            raise ValueError("此动作必须绑定已审阅计划。")
        if self.action in {"initialize", "confirm"} and not self.installationPlanId:
            raise ValueError("执行必须绑定原安装身份。")
        if self.action in {"initialize", "confirm"} and self.targetSourceRoot is None:
            raise ValueError("执行必须绑定计划中的目标 sourceRoot。")
        return self


def transfer_capabilities() -> dict[str, Any]:
    """声明独立转入动作与显式计划确认门。"""
    return {"name": "agent-ui-design-transfer", "endpoint": "/agent-ui-design-transfer/run",
        "transport": "ag-ui-sse", "actionField": "forwardedProps.agentUiDesignTransfer", "actions": ["plan", "initialize", "confirm", "get", "cancel"],
        "customEventName": "agent-ui-design-transfer", "stateSnapshotKey": "agentUiDesignTransfer",
        "workflowIndependent": True, "readOnly": False, "executionAvailable": True, "automaticReplay": False,
        'hostIntegration': 'reviewed-template-provider-extension', 'realRuntimeConnection': 'blocked_T27'}


async def _execute(request: AgentUiTransferAction, thread_id: str, run_id: str, report: ProgressReporter) -> dict[str, Any]:
    """线程执行文件与公开 SDK 操作，断流请求停止原任务并等待其保留现场。"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()

    def progress(stage: str) -> None:
        """仅把固定阶段投递到当前事件循环，不泄露工具原始输出。"""
        loop.call_soon_threadsafe(queue.put_nowait, stage)

    worker = asyncio.create_task(asyncio.to_thread(execute_transfer, request.workspace,
        installation_plan_id=request.installationPlanId, plan_id=request.planId, target_source_root=request.targetSourceRoot,
        action=request.action, thread_id=thread_id, run_id=run_id, progress=progress))
    try:
        while not worker.done() or not queue.empty():
            waiting = asyncio.create_task(queue.get())
            try:
                done, _ = await asyncio.wait({worker, waiting}, return_when=asyncio.FIRST_COMPLETED)
                if waiting in done:
                    stage = waiting.result()
                    await report(AgUiActionProgress(stage=stage, message=f"正在执行设计转入：{stage}", percent=0))
            finally:
                if not waiting.done():
                    waiting.cancel()
                    with suppress(asyncio.CancelledError):
                        await waiting
        return await worker
    except asyncio.CancelledError:
        workspace_process_registry.cancel_run(process_run_id(request.workspace, run_id))
        with suppress(Exception):
            await asyncio.shield(worker)
        raise


def public_transfer(record: dict[str, Any]) -> dict[str, Any]:
    """只投影状态、来源与恢复位置，不把备份源码或工具输出发布给 UI。"""
    return {key: record[key] for key in ("planId", "installationPlanId", "action", "state", "message", "recordPath",
        "recovery", "applied", "mapping", "validation", "startedAt", "finishedAt") if key in record}


def build_transfer_stream(*, payload: dict[str, Any], accept: str | None = None) -> AsyncIterator[str]:
    """规划、执行、查询与业务拒绝均发送完整生命周期，不替代正式设计确认。"""
    async def operation(report: ProgressReporter) -> AgUiActionResult:
        """绑定当前会话并从服务端实际磁盘计算差异；不执行用户传入的计划。"""
        request = AgentUiTransferAction.model_validate((payload.get("forwardedProps") or {}).get("agentUiDesignTransfer"))
        if not all(isinstance(payload.get(key), str) and payload[key] for key in ("threadId", "runId")):
            raise ValueError("设计转入规划必须提供当前 threadId 和 runId。")
        if request.action == "plan":
            installation = request.installationPlanId or await asyncio.to_thread(select_installation, request.workspace)
            plan = await asyncio.to_thread(prepare_transfer_plan, request.workspace,
                installation_plan_id=installation, target_source_root=request.targetSourceRoot)
            return AgUiActionResult(data={"plan": plan}, message="请审阅当前逐文件差异与恢复范围，再明确确认具体计划。")
        if request.action in {"initialize", "confirm"}:
            record = await _execute(request, payload["threadId"], payload["runId"], report)
        else:
            record = await asyncio.to_thread(read_transfer, request.workspace, request.planId)
            if not record:
                raise ValueError("没有对应转入任务，请先读取计划。")
            if request.action == "cancel":
                if record.get("threadId") != payload["threadId"]:
                    raise ValueError("当前会话不能停止其他会话的转入。")
                if record.get("state") == "running" and target_lock(request.workspace).locked():
                    workspace_process_registry.cancel_run(process_run_id(request.workspace, record["runId"]))
                    record = {**record, "state": "stopping", "message": "已请求停止，等待工具退出；已有文件保留。"}
        data = {"transfer": public_transfer(record)}
        if record.get("state") in {"failed", "stopped", "unknown", "stale"}:
            data["status"] = "failed"
        return AgUiActionResult(data=data, message=record.get("message") or "已读取当前转入状态。")

    return build_ag_ui_action_stream(payload=payload, event_name="agent-ui-design-transfer",
        state_key="agentUiDesignTransfer", run_id_prefix="agent-ui-design-transfer", progress_operation=operation,
        error_message_prefix="Agent UI 设计转入失败", accept=accept)
