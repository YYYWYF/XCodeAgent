"""平台 Python 后端日志的独立只读 AG-UI 协议。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.observability.backend_logs import get_backend_log_store
from app.protocols.ag_ui_action_stream import AgUiActionProgress, AgUiActionResult, ProgressReporter, build_ag_ui_action_stream

WATCH_TICKS = 20
WATCH_INTERVAL_SECONDS = 1.0


class BackendLogsInput(BaseModel):
    """仅允许读取和订阅固定平台日志，不接受任意文件或工作区。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["get", "watch"]


def backend_logs_capabilities() -> dict[str, Any]:
    """发布日志协议及有界窗口、刷新与模型日志开关语义。"""
    return {"name": "backend-logs", "endpoint": "/backend-logs/run", "transport": "ag-ui-sse",
            "actionField": "forwardedProps.backendLogs", "actions": ["get", "watch"],
            "customEventName": "backend-logs", "stateSnapshotKey": "backendLogs",
            "workflowIndependent": True, "refreshIntervalMs": 1000,
            "maxWindowBytes": 1048576, "maxWindowLines": 5000,
            "modelLogConfig": "MODEL_OUTPUT_LOG_ENABLED"}


def build_backend_logs_stream(*, payload: dict[str, Any], accept: str | None = None) -> AsyncIterator[str]:
    """通过公共 AG-UI 生命周期提供快照和可取消订阅。"""
    async def operation(report: ProgressReporter) -> AgUiActionResult:
        """有变化才推送日志窗口，有限订阅结束后由客户端续订。"""
        forwarded = payload.get("forwardedProps")
        try:
            request = BackendLogsInput.model_validate(forwarded.get("backendLogs") if isinstance(forwarded, dict) else None)
        except ValidationError:
            raise ValueError("backendLogs 只接受 action=get 或 action=watch。") from None
        store = get_backend_log_store()
        previous: tuple[int, str] | None = None
        current: dict[str, Any] = {}
        for index in range(WATCH_TICKS if request.action == "watch" else 1):
            if index:
                await asyncio.sleep(WATCH_INTERVAL_SECONDS)
            current = await asyncio.to_thread(store.snapshot)
            identity = (current["revision"], current["storageError"])
            if request.action == "watch" and identity != previous:
                await report(AgUiActionProgress(stage="logs", message="后端日志已更新", percent=0, data={"logs": current}))
                previous = identity
        return AgUiActionResult(data={"logs": current}, message="已读取后端日志。")

    return build_ag_ui_action_stream(payload=payload, event_name="backend-logs", state_key="backendLogs",
                                    run_id_prefix="backend-logs", progress_operation=operation,
                                    error_message_prefix="读取后端日志失败", accept=accept,
                                    emit_progress_text=False, register_workspace_run=False)
