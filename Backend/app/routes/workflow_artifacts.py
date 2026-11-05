"""独立只读 AG-UI 资料读取，不向客户端开放 checkpoint 或恢复输入。"""

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.workspace.content_artifacts import read_artifact

workflow_artifacts_router = APIRouter(prefix="/workflow-artifacts", tags=["workflow-artifacts"])
ARTIFACT_READ_BYTES = 16 * 1024 * 1024


class ArtifactReference(BaseModel):
    """只接受当前 JSON 内容地址与确切字节数。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    artifactId: str = Field(pattern=r"^[a-f0-9]{64}$")
    byteLength: int = Field(ge=0, le=ARTIFACT_READ_BYTES)
    mediaType: str = Field(pattern=r"^application/json$")


class ArtifactRead(BaseModel):
    """工作区资料只能按引用读取，不能指定任意文件路径。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    workspaceRoot: str = Field(min_length=1, max_length=4096)
    reference: ArtifactReference


def artifact_capabilities() -> dict[str, Any]:
    """发布当前资料读取边界和单次预算。"""
    return {"endpoint": "/workflow-artifacts/run", "transport": "ag-ui-sse",
            "readOnly": True, "maxBytes": ARTIFACT_READ_BYTES, "customEventName": "workflow-artifact"}


@workflow_artifacts_router.post("/run")
async def run_workflow_artifact(payload: dict[str, Any] = Body(...), accept: str | None = Header(default=None)) -> StreamingResponse:
    """按需读取单份不可变资料，并发出完整标准成功或失败生命周期。"""

    async def operation() -> AgUiActionResult:
        """在线程池验证大小和摘要，不阻塞其他运行的事件循环。"""
        request = ArtifactRead.model_validate((payload.get("forwardedProps") or {}).get("artifact"))
        content = await asyncio.to_thread(read_artifact, request.workspaceRoot,
            request.reference.model_dump(), max_bytes=ARTIFACT_READ_BYTES)
        return AgUiActionResult(data={"reference": request.reference.model_dump(), "content": content}, message="资料已读取。")

    return StreamingResponse(build_ag_ui_action_stream(payload=payload, event_name="workflow-artifact",
        state_key="workflowArtifact", run_id_prefix="artifact", operation=operation,
        error_message_prefix="资料读取失败", accept=accept, register_workspace_run=False), media_type="text/event-stream")
