"""AG-UI 公共生命周期"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
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


ActionOperation = Callable[[], Awaitable[AgUiActionResult]]
ErrorDataFactory = Callable[[Exception], dict[str, Any]]


def build_ag_ui_action_stream(
    *,
    payload: dict[str, Any],
    event_name: str,
    state_key: str,
    run_id_prefix: str,
    operation: ActionOperation,
    error_message_prefix: str,
    error_data: ErrorDataFactory | None = None,
    accept: str | None = None,
) -> AsyncIterator[str]:
    """
    业务异常会被编码为失败结果，并正常发送 RUN_FINISHED，使 HttpAgent可以用与成功响应相同的流结构消费失败信息。
    """

    encoder = EventEncoder(accept or "text/event-stream")
    thread_id = str(payload.get("threadId") or uuid4())
    run_id = str(payload.get("runId") or f"{run_id_prefix}-{uuid4().hex[:12]}")
    message_id = str(uuid4())

    async def stream() -> AsyncIterator[str]:
        yield encoder.encode(RunStartedEvent(threadId=thread_id, runId=run_id))
        yield encoder.encode(
            TextMessageStartEvent(messageId=message_id, role="assistant")
        )
        try:
            result = await operation()
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
