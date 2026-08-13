"""AG-UI 公共生命周期"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, AsyncIterator
from uuid import uuid4

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
from fastapi.encoders import jsonable_encoder


@dataclass(frozen=True)
class AgUiActionResult:
    """产品操作返回的业务数据和面向用户的助手消息。"""

    data: dict[str, Any]
    message: str


@dataclass(frozen=True)
class AgUiActionProgress:
    """描述长耗时产品操作当前阶段，并允许附带结构化状态。"""

    stage: str
    message: str
    percent: int
    detail: str = ""
    data: dict[str, Any] | None = None


@dataclass(frozen=True)
class AgUiActionTextDelta:
    """保存一次需要立即转发给客户端的模型文本增量。"""

    delta: str


ActionOperation = Callable[[], Awaitable[AgUiActionResult]]
ProgressReporter = Callable[[AgUiActionProgress], Awaitable[None]]
TextDeltaReporter = Callable[[str], Awaitable[None]]
ProgressActionOperation = Callable[[ProgressReporter], Awaitable[AgUiActionResult]]
StreamingActionOperation = Callable[
    [ProgressReporter, TextDeltaReporter], Awaitable[AgUiActionResult]
]
ErrorDataFactory = Callable[[Exception], dict[str, Any]]


def build_ag_ui_action_stream(
    *,
    payload: dict[str, Any],
    event_name: str,
    state_key: str,
    run_id_prefix: str,
    operation: ActionOperation | None = None,
    progress_operation: ProgressActionOperation | None = None,
    streaming_operation: StreamingActionOperation | None = None,
    error_message_prefix: str,
    error_data: ErrorDataFactory | None = None,
    accept: str | None = None,
    emit_progress_text: bool = True,
) -> AsyncIterator[str]:
    """
    业务异常会被编码为失败结果，并正常发送 RUN_FINISHED，使 HttpAgent可以用与成功响应相同的流结构消费失败信息。
    """

    operation_count = sum(
        candidate is not None
        for candidate in (operation, progress_operation, streaming_operation)
    )
    if operation_count != 1:
        raise ValueError(
            "operation、progress_operation 和 streaming_operation 必须且只能提供一个。"
        )

    encoder = EventEncoder(accept or "text/event-stream")
    thread_id = str(payload.get("threadId") or uuid4())
    run_id = str(payload.get("runId") or f"{run_id_prefix}-{uuid4().hex[:12]}")
    message_id = str(uuid4())

    async def stream() -> AsyncIterator[str]:
        """发送标准生命周期，并在长耗时操作期间持续转发阶段进度。"""

        yield encoder.encode(RunStartedEvent(threadId=thread_id, runId=run_id))
        yield encoder.encode(
            TextMessageStartEvent(messageId=message_id, role="assistant")
        )
        try:
            if progress_operation or streaming_operation:
                event_queue: asyncio.Queue[
                    AgUiActionProgress | AgUiActionTextDelta
                ] = asyncio.Queue()

                async def report(progress: AgUiActionProgress) -> None:
                    """把业务层报告的阶段进度放入当前 AG-UI 流队列。"""

                    await event_queue.put(progress)

                async def report_text(delta: str) -> None:
                    """把模型文本增量放入当前 AG-UI 流队列。"""

                    if delta:
                        await event_queue.put(AgUiActionTextDelta(delta=delta))

                operation_task = asyncio.create_task(
                    streaming_operation(report, report_text)
                    if streaming_operation
                    else progress_operation(report)  # type: ignore[misc]
                )
                try:
                    while not operation_task.done() or not event_queue.empty():
                        if event_queue.empty():
                            progress_task = asyncio.create_task(event_queue.get())
                            done, _pending = await asyncio.wait(
                                {operation_task, progress_task},
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            if progress_task not in done:
                                progress_task.cancel()
                                with suppress(asyncio.CancelledError):
                                    await progress_task
                                continue
                            stream_event = progress_task.result()
                        else:
                            stream_event = event_queue.get_nowait()

                        if isinstance(stream_event, AgUiActionTextDelta):
                            yield encoder.encode(
                                TextMessageContentEvent(
                                    messageId=message_id,
                                    delta=stream_event.delta,
                                )
                            )
                            continue

                        progress_payload: dict[str, Any] = {
                            "schemaVersion": 1,
                            "runId": run_id,
                            "threadId": thread_id,
                            "status": "in_progress",
                            "progress": {
                                "stage": stream_event.stage,
                                "message": stream_event.message,
                                "detail": stream_event.detail,
                                "percent": max(0, min(stream_event.percent, 100)),
                            },
                            **(stream_event.data or {}),
                        }
                        safe_progress = jsonable_encoder(progress_payload)
                        yield encoder.encode(CustomEvent(name=event_name, value=safe_progress))
                        yield encoder.encode(
                            StateSnapshotEvent(snapshot={state_key: safe_progress})
                        )
                        if not streaming_operation and emit_progress_text:
                            yield encoder.encode(
                                TextMessageContentEvent(
                                    messageId=message_id,
                                    delta=f"{stream_event.message}\n",
                                )
                            )
                    result = await operation_task
                finally:
                    if not operation_task.done():
                        operation_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await operation_task
            else:
                result = await operation()  # type: ignore[misc]
            response_payload: dict[str, Any] = {
                "schemaVersion": 1,
                "runId": run_id,
                "threadId": thread_id,
                "status": "completed",
                **result.data,
            }
            message = result.message
        except Exception as exc:
            response_payload = {
                "schemaVersion": 1,
                "runId": run_id,
                "threadId": thread_id,
                "status": "failed",
                **(error_data(exc) if error_data else {}),
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
            message = f"{error_message_prefix}：{type(exc).__name__}: {exc}"

        safe_payload = jsonable_encoder(response_payload)
        yield encoder.encode(CustomEvent(name=event_name, value=safe_payload))
        yield encoder.encode(StateSnapshotEvent(snapshot={state_key: safe_payload}))
        yield encoder.encode(
            TextMessageContentEvent(messageId=message_id, delta=message)
        )
        yield encoder.encode(TextMessageEndEvent(messageId=message_id))
        yield encoder.encode(
            RunFinishedEvent(
                threadId=thread_id,
                runId=run_id,
                result={state_key: safe_payload},
            )
        )

    return stream()
