"""实体建表独立 AG-UI 路由。"""

from typing import Any

from fastapi import APIRouter, Body, Header
from fastapi.responses import StreamingResponse

from app.protocols.entity_tables import build_entity_tables_stream


entity_tables_router = APIRouter(prefix="/entity-tables", tags=["entity-tables"])


@entity_tables_router.post("/run")
async def run_entity_tables(input_data: dict[str, Any] = Body(...), accept: str | None = Header(default="text/event-stream")) -> StreamingResponse:
    """通过标准事件流提供实体建表动作，不开放任意 SQL 接口。"""

    return StreamingResponse(build_entity_tables_stream(payload=input_data, accept=accept),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
