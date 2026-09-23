"""发起新迭代的独立 AG-UI 动作协议。"""

from __future__ import annotations

from typing import Any, AsyncIterator

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.iteration_service import (
    StartIterationRequest,
    start_iteration,
)


ITERATION_EVENT_NAME = "iteration-service"


def iteration_service_capabilities() -> dict[str, Any]:
    """发布发起新迭代动作的公开协议能力。"""

    return {
        "name": "iteration-service",
        "endpoint": "/iteration-service/run",
        "transport": "ag-ui-sse",
        "actions": ["start_iteration"],
        "customEventName": ITERATION_EVENT_NAME,
        "stateSnapshotKey": "iterationService",
        "workflowIndependent": True,
    }


def build_iteration_service_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """执行发起新迭代，清空规划产物并返回工作区状态。"""

    iteration_input = _iteration_input(payload)

    async def operation() -> AgUiActionResult:
        """执行迭代清理服务。"""

        request = StartIterationRequest.model_validate(iteration_input)
        result = start_iteration(request)
        data = result.model_dump(by_alias=True)
        message = f"已发起新迭代（分支 {request.branch_name}），规划产物已清空。"
        return AgUiActionResult(data=data, message=message)

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=ITERATION_EVENT_NAME,
        state_key="iterationService",
        run_id_prefix="iteration-service",
        operation=operation,
        error_message_prefix="发起新迭代失败",
        error_data=lambda _exc: {"action": "start_iteration"},
        accept=accept,
        workspace_root=str(iteration_input.get("workspaceRoot") or "") or None,
    )


def _iteration_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 AG-UI forwardedProps 中提取迭代业务输入。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    iteration = forwarded_props.get("iterationService")
    return iteration if isinstance(iteration, dict) else {}
