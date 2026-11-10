"""独立 Build 单项及批量任务重试 AG-UI 入口，不调用统一业务节点重试。"""

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.build_task_retry import load_task_retry_source, prepare_build_task_retry
from app.services.build_task_retry_queue import enqueue_build_task_retry


class BuildTaskRetryInput(BaseModel):
    """客户端只定位来源与任务，不允许提交任务状态、计划或检查点。"""
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    action: Literal["enqueue", "retry"]
    workspaceRoot: str = Field(min_length=1)
    sourceRunId: str = Field(min_length=1)
    sourceThreadId: str = Field(min_length=1)
    sessionId: str = Field(min_length=1)
    scope: Literal["single", "all_failed"] = "single"
    taskId: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_selection(self):
        """单任务必须明确 ID；批量范围由服务端解析，禁止夹带任务 ID。"""
        if (self.scope == "single") != (self.taskId is not None):
            raise ValueError("单任务重试必须提供 taskId，批量重试不得提供 taskId。")
        return self


def build_task_retry_capabilities() -> dict:
    """公开任务重试范围、身份、事件和运行中入队合同。"""
    return {"endpoint": "/build-task-retry/run", "transport": "ag-ui-sse",
        "actionField": "forwardedProps.buildTaskRetry", "actions": ["enqueue", "retry"],
        "customEventName": "build-task-retry", "stateSnapshotKey": "buildTaskRetry",
        "runId": "frontend", "enqueueRunId": "request-only",
        "retryScopes": ["single", "all_failed"]}


async def build_task_retry_stream(*, payload: dict, accept: str | None = None):
    """运行中只提交控制动作；已退出时把专用上下文交给标准 Workflow 事件流。"""
    context = None
    try:
        request = BuildTaskRetryInput.model_validate((payload.get("forwardedProps") or {}).get("buildTaskRetry"))
        run_id = payload.get("runId")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("必须提供前端生成的 runId。")
        if request.action == "retry":
            if payload.get("threadId") != request.sourceThreadId:
                raise ValueError("重试线程必须与原 Build 一致。")
            context = await prepare_build_task_retry(request, run_id)
        else:
            async def enqueue() -> AgUiActionResult:
                """等调度器落盘后返回入队回执，执行进度仍从原 Build 流发布。"""
                await load_task_retry_source(request)
                future = enqueue_build_task_retry(request.workspaceRoot, request.sourceRunId, request.taskId)
                result = await asyncio.shield(asyncio.wrap_future(future))
                return AgUiActionResult(data=result, message="任务已重新加入 Build 调度队列。")

            async for frame in build_ag_ui_action_stream(payload=payload, event_name="build-task-retry",
                    state_key="buildTaskRetry", run_id_prefix="build-task-retry", operation=enqueue,
                    error_message_prefix="任务重试失败", accept=accept, register_workspace_run=False):
                yield frame
            return
    except Exception as exc:
        message = str(exc)
        async def failed() -> AgUiActionResult:
            """准备失败也输出完整 AG-UI 生命周期，保留原任务及恢复入口。"""
            raise ValueError(message)

        async for frame in build_ag_ui_action_stream(payload=payload, event_name="build-task-retry",
                state_key="buildTaskRetry", run_id_prefix="build-task-retry", operation=failed,
                error_message_prefix="任务重试失败", accept=accept, register_workspace_run=False):
            yield frame
        return
    from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
    try:
        async for frame in build_workflow_ag_ui_stream(graph=context.graph, payload={},
                accept=accept, native_recovery_context=context):
            yield frame
    finally:
        if not context.runtime_started:
            from app.services.retry_preparation import abandon_prepared_retry
            from app.services.failed_run_cleanup import shield_run_cleanup
            await shield_run_cleanup(abandon_prepared_retry(workspace=context.workspace,
                source=context.source_execution, before=context.retry_lifecycle_before_handoff,
                new_run_id=context.new_run_id, lease=context.workspace_lease))
