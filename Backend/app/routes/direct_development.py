"""Direct 开发产物 AG-UI 路由。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Header
from fastapi.responses import StreamingResponse

from app.protocols.direct_development import build_direct_development_stream


direct_development_router = APIRouter(prefix="/direct-development", tags=["direct-development"])


@direct_development_router.post("/run")
async def run_direct_development(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """运行 Direct 实体和接口契约的独立 AG-UI 动作。"""

    return StreamingResponse(
        build_direct_development_stream(payload=input_data, accept=accept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
