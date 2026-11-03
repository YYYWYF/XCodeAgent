"""平台日志的 AG-UI 只读路由。"""

from typing import Any

from fastapi import APIRouter, Body, Header
from fastapi.responses import StreamingResponse

from app.protocols.backend_logs import build_backend_logs_stream

backend_logs_router = APIRouter(prefix="/backend-logs", tags=["backend-logs"])


@backend_logs_router.post("/run")
async def run_backend_logs(input_data: dict[str, Any] = Body(...), accept: str | None = Header(default="text/event-stream")) -> StreamingResponse:
    """以独立事件流读取固定用户目录日志，不参与工作流或业务锁。"""
    return StreamingResponse(build_backend_logs_stream(payload=input_data, accept=accept),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
