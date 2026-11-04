"""权限建表状态的独立 AG-UI 手动刷新协议，不进入 Build 图。"""

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Body, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.builtin_authorization_bootstrap import refresh_builtin_authorization

router = APIRouter()


class RefreshRequest(BaseModel):
    """限定当前工作区和原操作编号，刷新不能接收 SQL 或创建动作。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["refresh"]
    workspaceRoot: str = Field(min_length=1, max_length=4096)
    operationId: str = Field(pattern=r"^[1-9][0-9]*$", max_length=128)


def builtin_authorization_capabilities() -> dict[str, Any]:
    """公开单次状态刷新合同与独立事件身份。"""

    return {"endpoint": "/builtin-authorization/run", "transport": "ag-ui-sse",
            "actions": ["refresh"], "customEventName": "builtin-authorization",
            "stateSnapshotKey": "builtinAuthorization", "workflowIndependent": True}


def build_builtin_authorization_stream(payload: dict, accept: str | None = None):
    """让校验、查询错误和成功结果均经过完整 AG-UI 生命周期。"""

    async def operation() -> AgUiActionResult:
        """在线程内查询一次 SaaS，并投射与工作流相同的安全摘要。"""

        request = RefreshRequest.model_validate((payload.get("forwardedProps") or {}).get("builtinAuthorization") or {})
        result = await asyncio.to_thread(refresh_builtin_authorization, request.workspaceRoot, request.operationId)
        public = {"status": result["status"], "executor": result["executor"],
                  "datasourceMode": result["datasource_mode"], "schemaReady": result["schema_ready"],
                  "seedReady": result["seed_ready"], "operationId": result["operation_id"],
                  "operationStatus": result["operation_status"],
                  "initializedTables": result.get("initialized_tables", []),
                  "requiresManualAction": result["requires_manual_action"], "message": result["message"]}
        return AgUiActionResult(data={"authorizationBootstrap": public}, message=result["message"])

    return build_ag_ui_action_stream(payload=payload, event_name="builtin-authorization",
                                    state_key="builtinAuthorization", run_id_prefix="builtin-authorization",
                                    operation=operation, error_message_prefix="权限状态刷新失败", accept=accept)


@router.post("/builtin-authorization/run")
async def run_builtin_authorization(input_data: dict = Body(...), accept: str | None = Header(default="text/event-stream")):
    """响应手动刷新请求，不调度后续工作流节点。"""

    return StreamingResponse(build_builtin_authorization_stream(input_data, accept), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
