"""Direct 实体与 API 契约的独立 AG-UI 产品动作。"""

from __future__ import annotations

from typing import Any, AsyncIterator

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.direct_entity_design import (
    DirectEntityConfirmRequest,
    DirectEntityRequest,
    confirm_direct_entity_design,
    read_direct_entity_design,
)
from app.services.direct_api_contract import (
    DirectEndpointRequest,
    DirectEndpointSaveRequest,
    generate_direct_endpoint_draft,
    read_direct_endpoint_contract,
    save_direct_endpoint_contract,
)


def direct_development_capabilities() -> dict[str, Any]:
    """发布 Direct 开发产物的独立 AG-UI 能力。"""

    return {
        "name": "direct-development",
        "endpoint": "/direct-development/run",
        "transport": "ag-ui-sse",
        "actions": ["read_entity", "confirm_entity", "read_endpoint", "generate_endpoint", "save_endpoint"],
        "customEventName": "direct-development",
        "stateSnapshotKey": "directDevelopment",
        "workflowIndependent": True,
        "readOnly": False,
    }


def build_direct_development_stream(
    *, payload: dict[str, Any], accept: str | None = None,
) -> AsyncIterator[str]:
    """在完整 AG-UI 生命周期内读取或确认 Direct 正式产物。"""

    forwarded = payload.get("forwardedProps")
    request_input = forwarded.get("directDevelopment") if isinstance(forwarded, dict) else None
    request_input = request_input if isinstance(request_input, dict) else {}

    async def operation() -> AgUiActionResult:
        """严格按动作类型验证输入，确认后同步开发产物进度。"""

        action = str(request_input.get("action") or "")
        values = {key: value for key, value in request_input.items() if key != "action"}
        if action == "read_entity":
            design = read_direct_entity_design(DirectEntityRequest.model_validate(values))
            return AgUiActionResult(data={"action": action, "entity": design}, message="已读取实体 SQL 设计。")
        if action == "confirm_entity":
            request = DirectEntityConfirmRequest.model_validate(values)
            design = confirm_direct_entity_design(request)
            from app.services.development_artifacts import refresh_development_artifacts

            lifecycle = refresh_development_artifacts(request.workspace_root)
            return AgUiActionResult(
                data={"action": action, "entity": design, "lifecycle": lifecycle.model_dump(mode="json", by_alias=True)},
                message="实体 SQL 已确认，实体开发已完成。",
            )
        if action in {"read_endpoint", "generate_endpoint", "save_endpoint"}:
            if action == "save_endpoint":
                contract = save_direct_endpoint_contract(DirectEndpointSaveRequest.model_validate(values))
                message = "API 契约已保存并确认。"
            else:
                request = DirectEndpointRequest.model_validate(values)
                if action == "generate_endpoint":
                    import asyncio

                    contract = await asyncio.to_thread(generate_direct_endpoint_draft, request)
                    message = "模型已生成待确认的 API 契约草稿。"
                else:
                    contract = read_direct_endpoint_contract(request)
                    message = "已读取 API 契约。"
            return AgUiActionResult(data={"action": action, "endpoint": contract}, message=message)
        raise ValueError("directDevelopment.action 不受支持。")

    return build_ag_ui_action_stream(
        payload=payload,
        event_name="direct-development",
        state_key="directDevelopment",
        run_id_prefix="direct-development",
        operation=operation,
        error_message_prefix="Direct 开发产物操作失败",
        error_data=lambda _exc: {"action": str(request_input.get("action") or "")},
        accept=accept,
    )
