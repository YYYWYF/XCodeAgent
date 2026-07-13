from __future__ import annotations

import asyncio
from threading import Lock
from typing import Any, AsyncIterator

from ag_ui.core import (
    CustomEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder


class WorkflowRunRegistry:
    """Process-local registry for actively streaming workflow tasks."""

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


def build_workflow_cancellation_ag_ui_stream(
    *,
    thread_id: str,
    run_id: str,
    target_run_id: str,
    accept: str | None = None,
) -> AsyncIterator[str]:
    """Acknowledge a workflow cancellation through a normal AG-UI stream."""

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
        yield encoder.encode(CustomEvent(name="workflow-run-control", value=result))
        yield encoder.encode(StateSnapshotEvent(snapshot={"workflowRunControl": result}))
        yield encoder.encode(
            RunFinishedEvent(
                threadId=thread_id,
                runId=run_id,
                result={"workflowRunControl": result},
            )
        )

    return stream()
