"""独立 Endpoint API 设计配置与详情的 AG-UI 协议适配。"""

from __future__ import annotations

from typing import Any, AsyncIterator

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.endpoint_design_detail import (
    EndpointDesignDetailRequest,
    EndpointDesignPrepareRequest,
    EndpointDesignSaveRequest,
    prepare_endpoint_design,
    read_endpoint_design_detail,
    save_endpoint_design,
)


ENDPOINT_DESIGNS_EVENT_NAME = "endpoint-designs"


def endpoint_designs_capabilities() -> dict[str, Any]:
    """发布 Endpoint 设计独立配置与查询的 AG-UI 能力。"""

    return {
        "name": "endpoint-designs",
        "endpoint": "/endpoint-designs/run",
        "transport": "ag-ui-sse",
        "actions": ["get", "prepare", "save"],
        "customEventName": ENDPOINT_DESIGNS_EVENT_NAME,
        "stateSnapshotKey": "endpointDesigns",
        "workflowIndependent": True,
        "readOnly": False,
    }


def build_endpoint_designs_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """构造 Endpoint 设计独立配置与查询的完整 AG-UI 生命周期。"""

    request_input = _endpoint_designs_input(payload)

    async def operation() -> AgUiActionResult:
        """执行严格校验后的独立 Endpoint 配置动作，不触发主工作流。"""

        action = str(request_input.get("action") or "get")
        request_values = {key: value for key, value in request_input.items() if key != "action"}
        if action == "get":
            request = EndpointDesignDetailRequest.model_validate(request_values)
            detail = read_endpoint_design_detail(request)
            return AgUiActionResult(data={"action": action, "detail": detail}, message="已读取 Endpoint API 映射结果。")
        if action == "prepare":
            request = EndpointDesignPrepareRequest.model_validate(request_values)
            preparation = prepare_endpoint_design(request)
            return AgUiActionResult(data={"action": action, "preparation": preparation}, message="已准备 Endpoint API 映射配置。")
        if action == "save":
            request = EndpointDesignSaveRequest.model_validate(request_values)
            saved = save_endpoint_design(request)
            return AgUiActionResult(data={"action": action, "saved": saved, "detail": saved.get("detail")}, message="Endpoint API 映射配置已保存。")
        raise ValueError("endpointDesigns.action 必须是 get、prepare 或 save。")

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=ENDPOINT_DESIGNS_EVENT_NAME,
        state_key="endpointDesigns",
        run_id_prefix="endpoint-designs",
        operation=operation,
        error_message_prefix="Endpoint API 映射操作失败",
        error_data=lambda _exc: {"action": str(request_input.get("action") or "get")},
        accept=accept,
    )


def _endpoint_designs_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 forwardedProps 中提取 Endpoint 设计查询输入。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    value = forwarded_props.get("endpointDesigns")
    return value if isinstance(value, dict) else {}
