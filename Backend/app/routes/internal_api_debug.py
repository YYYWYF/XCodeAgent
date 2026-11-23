"""内部 API 调试的独立 AG-UI 入口。"""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from pydantic import Field, TypeAdapter, ValidationError

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.internal_api_debug import DescribeInternalDebug, SendInternalDebug, describe_internal_debug, send_internal_debug


router = APIRouter()
_INPUT = TypeAdapter(Annotated[DescribeInternalDebug | SendInternalDebug, Field(discriminator="action")])


@router.post("/internal-api-debug/run")
async def run_internal_api_debug(payload: dict[str, Any], accept: str | None = Header(None)) -> StreamingResponse:
    """读取接口定义或执行一次调试，维护完整的 AG-UI 生命周期。"""
    async def execute() -> AgUiActionResult:
        """只返回当前调试专用快照，业务对话不接收请求和凭据。"""
        try:
            data = _INPUT.validate_python(payload["forwardedProps"]["internalApiDebug"])
        except (ValidationError, TypeError, KeyError):
            raise ValueError("内部接口调试参数无效，请检查输入。") from None
        if isinstance(data, DescribeInternalDebug):
            detail = await asyncio.to_thread(describe_internal_debug, data)
            return AgUiActionResult(data={"endpoint": detail}, message="已读取接口定义。")
        response = await send_internal_debug(data)
        return AgUiActionResult(data={"response": response}, message="已收到内部接口响应。")

    stream = build_ag_ui_action_stream(
        payload=payload, event_name="internal-api-debug", state_key="internalApiDebug",
        run_id_prefix="internal-api-debug", operation=execute, accept=accept,
        error_message_prefix="内部接口调试失败", register_workspace_run=False,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-store"})
