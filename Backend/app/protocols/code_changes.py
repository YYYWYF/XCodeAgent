"""代码变更产品操作的 AG-UI 协议适配。"""

from __future__ import annotations

from typing import Any, AsyncIterator

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.code_change_revert import CodeChangeRevertRequest, revert_code_change_set


CODE_CHANGES_EVENT_NAME = "code-changes"


def code_changes_capabilities() -> dict[str, Any]:
    """公布独立代码变更操作支持的 AG-UI 契约。"""

    return {
        "name": "code-changes",
        "endpoint": "/code-changes/run",
        "transport": "ag-ui-sse",
        "actions": ["revert"],
        "customEventName": CODE_CHANGES_EVENT_NAME,
        "stateSnapshotKey": "codeChangesAction",
        "workflowIndependent": True,
    }


def build_code_changes_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """构建代码变更撤销操作的完整 AG-UI 生命周期流。"""

    action_input = _code_changes_action_input(payload)
    action = action_input.get("action")

    async def operation() -> AgUiActionResult:
        """执行已校验的代码变更产品操作。"""

        request = CodeChangeRevertRequest.model_validate(action_input)
        result = revert_code_change_set(request)
        return AgUiActionResult(
            data=result.model_dump(by_alias=True),
            message=f"已撤销本次对 {len(result.reverted_paths)} 个文件的修改。",
        )

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=CODE_CHANGES_EVENT_NAME,
        state_key="codeChangesAction",
        run_id_prefix="code-changes",
        operation=operation,
        error_message_prefix="撤销代码变更失败",
        error_data=lambda _exc: {"action": action},
        accept=accept,
        workspace_root=str(action_input.get("workspaceRoot") or "") or None,
    )


def _code_changes_action_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 AG-UI forwardedProps 中读取代码变更操作参数。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    action_input = forwarded_props.get("codeChangesAction")
    return action_input if isinstance(action_input, dict) else {}
