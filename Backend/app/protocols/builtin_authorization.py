"""独立权限状态查询和调试补丁的 AG-UI 协议。"""

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Body, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.protocols.ag_ui_action_stream import AgUiActionProgress, AgUiActionResult, ProgressReporter, build_ag_ui_action_stream
from app.services.builtin_authorization_bootstrap import refresh_builtin_authorization
from app.services.builtin_authorization_patch import patch_view, refresh_patch
from app.services.builtin_authorization_seed_runtime import refresh_builtin_authorization_seed
from app.services.builtin_authorization_orchestrator import ensure_builtin_authorization_task, resume_builtin_authorization_task
from app.services.backend_instance import current_backend_instance

router = APIRouter()


class RefreshRequest(BaseModel):
    """限定当前工作区和原操作编号，刷新不能接收 SQL 或创建动作。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["refresh"]
    workspaceRoot: str = Field(min_length=1, max_length=4096)
    operationKind: Literal["schema", "seed"] = "schema"
    operationId: str = Field(pattern=r"^[1-9][0-9]*$", max_length=128)


class PatchRequest(BaseModel):
    """限制补丁输入，权限数据和原操作身份均由服务端确定。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["get", "run_patch", "refresh_patch", "resume"]
    workspaceRoot: str = Field(min_length=1, max_length=4096)


async def _patch_action(request: PatchRequest, report: ProgressReporter) -> AgUiActionResult:
    """分段报告真实检查和核验过程，刷新只查询原操作。"""

    workspace = request.workspaceRoot
    if request.action == "get":
        view = await asyncio.to_thread(patch_view, workspace)
    elif request.action == "refresh_patch":
        await report(AgUiActionProgress("query", "正在查询原操作…", 0))
        result = await asyncio.to_thread(refresh_patch, workspace)
        view = await asyncio.to_thread(patch_view, workspace, result)
    elif request.action == "resume":
        view = await asyncio.to_thread(resume_builtin_authorization_task, workspace)
    else:
        await report(AgUiActionProgress("schema", "正在调度权限初始化…", 0))
        view = await asyncio.to_thread(ensure_builtin_authorization_task, workspace, source="patch")
    return AgUiActionResult(data={"patch": view, "backendInstanceId": current_backend_instance().instance_id},
                           message=view["message"] if view else "尚无权限补丁记录。")


def builtin_authorization_capabilities() -> dict[str, Any]:
    """公开单次状态刷新合同与独立事件身份。"""

    return {"endpoint": "/builtin-authorization/run", "transport": "ag-ui-sse",
            "actions": ["refresh", "get", "run_patch", "refresh_patch", "resume"], "operationKinds": ["schema", "seed"], "customEventName": "builtin-authorization",
            "stateSnapshotKey": "builtinAuthorization", "workflowIndependent": True}


def build_builtin_authorization_stream(payload: dict, accept: str | None = None):
    """让校验、查询错误和成功结果均经过完整 AG-UI 生命周期。"""

    async def operation(report: ProgressReporter) -> AgUiActionResult:
        """执行独立权限动作，并保持原工作流刷新结果合同。"""

        action = (payload.get("forwardedProps") or {}).get("builtinAuthorization") or {}
        if isinstance(action, dict) and action.get("action") in ("get", "run_patch", "refresh_patch", "resume"):
            return await _patch_action(PatchRequest.model_validate(action), report)
        request = RefreshRequest.model_validate(action)
        query = refresh_builtin_authorization_seed if request.operationKind == "seed" else refresh_builtin_authorization
        result = await asyncio.to_thread(query, request.workspaceRoot, request.operationId)
        public = {"status": result["status"], "executor": result["executor"],
                  "datasourceMode": result["datasource_mode"], "schemaReady": result["schema_ready"],
                  "seedReady": result["seed_ready"], "operationId": result["operation_id"],
                  "operationStatus": result["operation_status"],
                  "seedOperationId": result.get("seed_operation_id"),
                  "seedOperationStatus": result.get("seed_operation_status"),
                  "nextAction": result.get("next_action"), "failureCategory": result.get("failure_category"),
                  "initializedTables": result.get("initialized_tables", []),
                  "requiresManualAction": result["requires_manual_action"], "message": result["message"]}
        return AgUiActionResult(data={"authorizationBootstrap": public}, message=result["message"])

    return build_ag_ui_action_stream(payload=payload, event_name="builtin-authorization",
                                    state_key="builtinAuthorization", run_id_prefix="builtin-authorization",
                                    progress_operation=operation, error_message_prefix="内置权限操作失败", accept=accept)


@router.post("/builtin-authorization/run")
async def run_builtin_authorization(input_data: dict = Body(...), accept: str | None = Header(default="text/event-stream")):
    """响应独立编排与只读查询动作，不调度 Build 节点。"""

    return StreamingResponse(build_builtin_authorization_stream(input_data, accept), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
