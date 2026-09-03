"""独立数据源目录的 AG-UI HTTP 路由。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Header
from fastapi.responses import StreamingResponse

from app.protocols.data_sources import (
    DataSourceActionName,
    build_data_sources_ag_ui_stream,
)


data_sources_router = APIRouter(prefix="/data-sources", tags=["data-sources"])


def _data_source_response(
    *,
    action: DataSourceActionName,
    input_data: dict[str, Any],
    accept: Optional[str],
) -> StreamingResponse:
    """创建指定数据源动作的 AG-UI SSE 响应。"""

    return StreamingResponse(
        build_data_sources_ag_ui_stream(
            action=action,
            payload=input_data,
            accept=accept,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@data_sources_router.post("/list")
async def list_data_sources(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """读取独立数据源目录。"""

    return _data_source_response(action="list", input_data=input_data, accept=accept)


@data_sources_router.post("/create")
async def create_data_source(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """创建独立数据源。"""

    return _data_source_response(action="create", input_data=input_data, accept=accept)


@data_sources_router.post("/update")
async def update_data_source(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """更新独立数据源。"""

    return _data_source_response(action="update", input_data=input_data, accept=accept)


@data_sources_router.post("/delete")
async def delete_data_source(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """删除独立数据源。"""

    return _data_source_response(action="delete", input_data=input_data, accept=accept)


@data_sources_router.post("/validate")
async def validate_data_source(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """校验尚未保存或已保存的数据源。"""

    return _data_source_response(action="validate", input_data=input_data, accept=accept)


@data_sources_router.post("/detail")
async def detail_data_source(
    input_data: dict[str, Any] = Body(...),
    accept: Optional[str] = Header(default="text/event-stream"),
) -> StreamingResponse:
    """读取指定外部 API 接口的完整配置详情。"""

    return _data_source_response(action="detail", input_data=input_data, accept=accept)
