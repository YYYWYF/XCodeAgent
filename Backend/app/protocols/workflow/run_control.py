"""管理当前进程内活跃主工作流的注册与取消。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from threading import Lock
from typing import Any, AsyncIterator

from ag_ui.core import (
    CustomEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    StateSnapshotEvent,
)
from ag_ui.encoder import EventEncoder

from app.services.application_lifecycle import (
    application_lifecycle_payload,
    end_workbench_execution,
    load_application_lifecycle,
    stop_workbench_execution,
)
from app.domain.application_lifecycle import PendingInteractionType
from app.workspace.task_documents import (
    build_task_plan_json_path,
    load_build_task_plan_json,
    write_build_task_plan_json,
)


class WorkflowRunRegistry:
    """当前进程内正在流式运行的主工作流任务注册表。

    该实现专门匹配单进程桌面后端；如果改为多 Worker 部署，必须替换成
    跨进程共享的取消协调机制。
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def register(self, run_id: str, task: asyncio.Task[Any]) -> None:
        with self._lock:
            self._tasks[run_id] = task

    def unregister(self, run_id: str, task: asyncio.Task[Any] | None = None) -> None:
        with self._lock:
            current = self._tasks.get(run_id)
            if current is not None and (task is None or current is task):
                self._tasks.pop(run_id, None)

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(run_id)
        return bool(task and not task.done() and task.cancel())


workflow_run_registry = WorkflowRunRegistry()


def abandon_pending_build_task_plan(workspace: str, *, run_id: str) -> bool:
    """把指定 execution 正在等待确认的 DAG 标记为已放弃，其他计划结束不改动 DAG。"""

    lifecycle = load_application_lifecycle(workspace)
    execution = lifecycle.active_executions.get(run_id) if lifecycle else None
    pending = execution.pending_interaction if execution else None
    pending_mode = str(pending.payload.get("mode") or "") if pending else ""
    if pending is None or (
        pending.type != PendingInteractionType.TASK_PLAN_CONFIRMATION
        and pending_mode != "build_task_plan_confirmation"
    ):
        return False

    path = build_task_plan_json_path({"workspace": workspace})
    if not path.exists():
        raise ValueError("工作区中不存在待放弃的 build-task-plan.json。")
    plan = load_build_task_plan_json(path)
    confirmation_status = str(plan.get("confirmation_status") or "")
    if confirmation_status not in {"pending", "abandoned"}:
        raise ValueError("当前 Build DAG 已不处于待确认状态，不能放弃。")
    if confirmation_status == "abandoned":
        return True
    write_build_task_plan_json(
        {"workspace": workspace},
        {
            **plan,
            "confirmation_status": "abandoned",
            "abandoned_at": datetime.now(UTC).isoformat(),
        },
    )
    return True


def build_workflow_cancellation_ag_ui_stream(
    *,
    thread_id: str,
    run_id: str,
    target_run_id: str,
    accept: str | None = None,
) -> AsyncIterator[str]:
    """通过正常的 AG-UI 事件流确认主工作流取消请求。"""

    encoder = EventEncoder(accept or "text/event-stream")
    message_id = f"cancel:{run_id}"

    async def stream() -> AsyncIterator[str]:
        cancelled = workflow_run_registry.cancel(target_run_id)
        status = "cancel_requested" if cancelled else "not_running"
        message = (
            "已请求停止正在运行的 Workflow。"
            if cancelled
            else "目标 Workflow 已结束，无需停止。"
        )
        result = {
            "status": status,
            "targetRunId": target_run_id,
            "message": message,
        }

        yield encoder.encode(RunStartedEvent(threadId=thread_id, runId=run_id))
        yield encoder.encode(TextMessageStartEvent(messageId=message_id, role="assistant"))
        yield encoder.encode(TextMessageContentEvent(messageId=message_id, delta=message))
        yield encoder.encode(TextMessageEndEvent(messageId=message_id))
        yield encoder.encode(
            RunFinishedEvent(
                threadId=thread_id,
                runId=run_id,
                result={"workflowRunControl": result},
            )
        )

    return stream()


def build_workflow_plan_control_ag_ui_stream(
    *,
    action: str,
    workspace: str,
    target_run_id: str,
    thread_id: str,
    run_id: str,
    accept: str | None = None,
) -> AsyncIterator[str]:
    """通过主 AG-UI 端点执行不启动 Graph 的计划控制动作。"""

    encoder = EventEncoder(accept or "text/event-stream")
    message_id = f"plan-control:{run_id}"

    async def stream() -> AsyncIterator[str]:
        if action not in {"stop", "end"}:
            raise ValueError(f"不支持的计划控制动作：{action}")
        if not target_run_id:
            raise ValueError("计划控制动作缺少目标 runId。")
        abandoned_task_plan = (
            abandon_pending_build_task_plan(workspace, run_id=target_run_id)
            if action == "end"
            else False
        )
        lifecycle = application_lifecycle_payload(
            end_workbench_execution(workspace, run_id=target_run_id)
            if action == "end"
            else stop_workbench_execution(workspace, run_id=target_run_id)
        )
        message = (
            "Build DAG 已放弃，当前流程已停止。"
            if abandoned_task_plan
            else "计划已结束，工作区已恢复自由输入。"
            if action == "end"
            else "计划执行已暂停，可继续执行、调整计划或结束。"
        )
        workflow = {
            "runId": run_id,
            "threadId": thread_id,
            "summary": {
                "status": "cancelled",
                "phase": "plan_control",
                "message": message,
                "lifecycle": lifecycle,
            },
            "events": [],
            "state": {"status": "cancelled", "phase": "plan_control", "lifecycle": lifecycle},
            "result": {"status": "cancelled", "phase": "plan_control", "lifecycle": lifecycle},
        }
        yield encoder.encode(RunStartedEvent(threadId=thread_id, runId=run_id))
        yield encoder.encode(TextMessageStartEvent(messageId=message_id, role="assistant"))
        # 控制动作写入成功后立即广播生命周期，所有工作台区域共享同一 revision。
        yield encoder.encode(CustomEvent(name="application-lifecycle", value=lifecycle))
        yield encoder.encode(CustomEvent(name="workflow-run", value=workflow))
        yield encoder.encode(StateSnapshotEvent(snapshot={"workflow": workflow}))
        yield encoder.encode(TextMessageContentEvent(messageId=message_id, delta=message))
        yield encoder.encode(TextMessageEndEvent(messageId=message_id))
        yield encoder.encode(
            RunFinishedEvent(
                threadId=thread_id,
                runId=run_id,
                result={"workflow": workflow},
            )
        )

    return stream()
