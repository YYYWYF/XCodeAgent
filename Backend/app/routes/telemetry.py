"""埋点独立 AG-UI 路由。"""

from typing import Any

from fastapi import APIRouter, Body, Header
from fastapi.responses import StreamingResponse

from app.protocols.telemetry import build_telemetry_ag_ui_stream


telemetry_router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@telemetry_router.post("/run")
async def run_telemetry(
    input_data: dict[str, Any] = Body(...),
    accept: str | None = Header(default="text/event-stream"),
) -> StreamingResponse:
    """通过独立 AG-UI 生命周期提交埋点，不等待第三方 POST 完成。"""

    return StreamingResponse(
        build_telemetry_ag_ui_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
