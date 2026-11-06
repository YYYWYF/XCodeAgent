"""正式目标依赖准备的独立 AG-UI 控制面，不进入设计或业务 Runtime。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.protocols.ag_ui_action_stream import (
    AgUiActionProgress, AgUiActionResult, ProgressReporter, build_ag_ui_action_stream,
)
from app.services.agent_ui_dependency_install import execute_dependency_plan, process_run_id, read_installation, target_lock
from app.services.agent_ui_dependency_plan import prepare_dependency_plan, target_root
from app.services.workspace_process_registry import workspace_process_registry


class AgentUiDependencyAction(BaseModel):
    """限制输入为明确目标、已发布组合和精确批准身份，不接受任意命令。"""

    model_config = ConfigDict(extra="forbid")
    workspace: str = Field(min_length=1, max_length=4096)
    action: Literal["plan", "confirm", "get", "cancel"]
    source: Literal["registry", "local-vendor"] = "registry"
    entry: str = Field(default="", max_length=512)
    planId: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")
    reviewId: str = Field(default="", pattern=r"^(?:[a-f0-9]{32})?$")

    @model_validator(mode="after")
    def require_approval_identity(self) -> AgentUiDependencyAction:
        """执行、查询与停止必须绑定同一具体计划。"""
        if self.action != "plan" and not self.planId:
            raise ValueError("当前动作必须提供安装计划身份。")
        if self.action == "confirm" and not self.reviewId:
            raise ValueError("确认必须绑定本次审阅身份；重新计划不能沿用旧批准。")
        return self


def dependency_capabilities() -> dict[str, Any]:
    """通过 health 发布当前独立产品动作与限制。"""
    return {"name": "agent-ui-dependencies", "endpoint": "/agent-ui-dependencies/run",
            "transport": "ag-ui-sse", "actionField": "forwardedProps.agentUiDependencies",
            "actions": ["plan", "confirm", "get", "cancel"], "customEventName": "agent-ui-dependencies",
            "stateSnapshotKey": "agentUiDependencies", "workflowIndependent": True,
            "target": "frontend", "managers": ["npm", "pnpm"], "workspaceSupported": False,
            "localVendorManagers": ["pnpm"], "approval": "explicit-plan-digest", "automaticReplay": False}


def public_installation(record: dict[str, Any]) -> dict[str, Any]:
    """公开运行证据与恢复位置，不把备份内容或任意日志加入事件流。"""
    result = {key: record[key] for key in ("planId", "threadId", "runId", "state", "message", "recovery",
            "startedAt", "finishedAt", "applied", "commands", "installation", "styles", "stage") if key in record}
    if record.get("planId"):
        result["recordPath"] = f".devagentstudio/runtime/agent-ui-dependencies/{record['planId']}/record.json"
    return result


async def _execute(request: AgentUiDependencyAction, thread_id: str, run_id: str,
                   report: ProgressReporter) -> dict[str, Any]:
    """同步安装持续投影进度；断流先取消并等待进程收口，不能重发原操作。"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()

    def progress(stage: str) -> None:
        """从命令线程投递阶段，不让关闭的订阅阻塞写入线程收口。"""
        loop.call_soon_threadsafe(queue.put_nowait, stage)

    worker = asyncio.create_task(asyncio.to_thread(execute_dependency_plan, request.workspace,
        plan_id=request.planId, source=request.source, entry=request.entry,
        thread_id=thread_id, run_id=run_id, review_id=request.reviewId, progress=progress))
    try:
        while not worker.done() or not queue.empty():
            waiting = asyncio.create_task(queue.get())
            try:
                done, _ = await asyncio.wait({worker, waiting}, return_when=asyncio.FIRST_COMPLETED)
                if waiting in done:
                    stage = waiting.result()
                    await report(AgUiActionProgress(stage=stage, message=f"正在执行目标 {stage}…", percent=0))
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


def build_dependency_stream(*, payload: dict[str, Any], accept: str | None = None) -> AsyncIterator[str]:
    """所有动作与业务拒绝均使用公共 AG-UI 完整消息、快照和结束生命周期。"""
    async def operation(report: ProgressReporter) -> AgUiActionResult:
        """只消费服务器重新计算的计划，确认和取消不互相替代。"""
        request = AgentUiDependencyAction.model_validate((payload.get("forwardedProps") or {}).get("agentUiDependencies"))
        thread_id = payload.get("threadId")
        run_id = payload.get("runId")
        if not isinstance(thread_id, str) or not thread_id or not isinstance(run_id, str) or not run_id:
            raise ValueError("依赖动作必须提供当前 threadId 和 runId。")
        root, _ = target_root(request.workspace)
        if request.action == "plan":
            plan = await asyncio.to_thread(prepare_dependency_plan, root, source=request.source, entry=request.entry, review_id=uuid4().hex)
            return AgUiActionResult(data={"plan": plan}, message="请审阅依赖、逐文件样式差异及恢复范围，再明确确认此安装计划。")
        if request.action == "confirm":
            record = await _execute(request, thread_id, run_id, report)
        else:
            record = await asyncio.to_thread(read_installation, root, request.planId)
            if not record:
                raise ValueError("没有对应安装任务，请先读取或制定计划。")
            if request.action == "cancel":
                if record.get("threadId") != thread_id:
                    raise ValueError("当前会话不能停止其他会话的安装任务。")
                if record.get("state") == "running" and target_lock(root).locked():
                    workspace_process_registry.cancel_run(process_run_id(root, record["runId"]))
                    record = {**record, "state": "stopping", "message": "已请求停止，等待当前命令安全退出；已有变更保留。"}
        result: dict[str, Any] = {"installation": public_installation(record)}
        if record.get("state") in {"failed", "unknown", "stopped", "stale"}:
            result["status"] = "failed"
        return AgUiActionResult(data=result, message=record.get("message") or "已读取当前安装证据。")

    return build_ag_ui_action_stream(payload=payload, event_name="agent-ui-dependencies",
        state_key="agentUiDependencies", run_id_prefix="agent-ui-dependencies", progress_operation=operation,
        error_message_prefix="Agent UI 依赖准备失败", accept=accept)
