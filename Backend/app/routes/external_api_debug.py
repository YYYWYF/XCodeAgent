"""外部接口调试使用独立 AG-UI 动作，不写工作流状态。"""
from typing import Any

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.external_api_debug import DebugInput, send_debug_request

router = APIRouter()


@router.post("/external-api-debug/run")
async def run_external_api_debug(payload: dict[str, Any], accept: str | None = Header(None)) -> StreamingResponse:
    """执行用户明确发送的请求；失败结果不包含请求数据。"""
    async def execute() -> AgUiActionResult:
        """只投影本轮结果，凭据不进入消息或 checkpoint。"""
        try:
            data = DebugInput.model_validate(payload["forwardedProps"]["externalApiDebug"])
        except (ValidationError, TypeError, KeyError):
            raise ValueError("调试请求格式无效，请检查输入。") from None
        result = await send_debug_request(data)
        return AgUiActionResult(data={"response": result}, message="已收到外部接口响应。")

    stream = build_ag_ui_action_stream(payload=payload, event_name="external-api-debug",
        state_key="externalApiDebug", run_id_prefix="external-api-debug", operation=execute,
        accept=accept, error_message_prefix="调试请求失败", register_workspace_run=False)
    return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-store"})
