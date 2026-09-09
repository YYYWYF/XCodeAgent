"""Endpoint API 设计独立配置 AG-UI HTTP 路由。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Header
from fastapi.responses import StreamingResponse

from app.protocols.endpoint_designs import build_endpoint_designs_ag_ui_stream


endpoint_designs_router = APIRouter(prefix="/endpoint-designs", tags=["endpoint-designs"])


@endpoint_designs_router.post("/run")
async def run_endpoint_designs(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """通过独立 AG-UI 流读取、准备或保存 Endpoint API 设计正式产物。"""

    return StreamingResponse(
        build_endpoint_designs_ag_ui_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
