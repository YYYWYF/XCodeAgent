"""页面开发概览的独立只读 AG-UI 入口。"""

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Body, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.page_artifact_overview import page_artifact_overview

page_artifacts_router = APIRouter(prefix="/page-artifacts", tags=["page-artifacts"])


class PageArtifactRead(BaseModel):
    """仅接受工作区和页面稳定身份，不允许客户端传入状态或任意文件路径。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["get"]
    workspaceRoot: str = Field(min_length=1, max_length=4096)
    pageId: str = Field(min_length=1, max_length=256)


def page_artifacts_capabilities() -> dict[str, Any]:
    """公布当前只读页面概览合同。"""
    return {"endpoint": "/page-artifacts/run", "transport": "ag-ui-sse", "actions": ["get"],
            "readOnly": True, "customEventName": "page-artifact", "stateSnapshotKey": "pageArtifact"}


@page_artifacts_router.post("/run")
async def run_page_artifact(payload: dict[str, Any] = Body(...), accept: str | None = Header(default=None)) -> StreamingResponse:
    """读取页面概览，所有业务失败仍发送完整 AG-UI 生命周期。"""
    async def operation() -> AgUiActionResult:
        """在线程池读取当前事实，避免磁盘查询阻塞其他工作流。"""
        forwarded = payload.get("forwardedProps")
        request = PageArtifactRead.model_validate(forwarded.get("pageArtifact") if isinstance(forwarded, dict) else None)
        overview = await asyncio.to_thread(page_artifact_overview, request.workspaceRoot, request.pageId)
        return AgUiActionResult(data={"overview": overview}, message="已读取页面开发概览。")

    return StreamingResponse(build_ag_ui_action_stream(
        payload=payload, event_name="page-artifact", state_key="pageArtifact", run_id_prefix="page-artifact",
        operation=operation, error_message_prefix="页面概览读取失败", accept=accept,
        register_workspace_run=False,
    ), media_type="text/event-stream")
