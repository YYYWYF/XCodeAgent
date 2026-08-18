"""二次修改版本控制的独立 AG-UI 动作协议。"""

from __future__ import annotations

from typing import Any, AsyncIterator

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.version_control import (
    CommitVersionControlRequest,
    InspectVersionControlRequest,
    commit_version_control,
    inspect_version_control,
)


VERSION_CONTROL_EVENT_NAME = "version-control"


def version_control_capabilities() -> dict[str, Any]:
    """发布独立版本控制动作的公开协议能力。"""

    return {
        "name": "version-control",
        "endpoint": "/version-control/run",
        "transport": "ag-ui-sse",
        "actions": ["inspect", "commit"],
        "customEventName": VERSION_CONTROL_EVENT_NAME,
        "stateSnapshotKey": "versionControl",
        "workflowIndependent": True,
    }


def build_version_control_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """执行状态复核或显式提交，并发送完整 AG-UI 生命周期。"""

    version_control_input = _version_control_input(payload)
    action = version_control_input.get("action")

    async def operation() -> AgUiActionResult:
        """按动作分派到确定性的 Git 服务。"""

        if action == "inspect":
            request = InspectVersionControlRequest.model_validate(version_control_input)
            snapshot = inspect_version_control(request)
            data = {"action": action, "snapshot": snapshot.model_dump(by_alias=True)}
            message = "已重新读取当前 Git 状态。"
        elif action == "commit":
            request = CommitVersionControlRequest.model_validate(version_control_input)
            result = commit_version_control(request)
            data = result.model_dump(by_alias=True)
            message = f"已提交本次修改：{result.commit_sha[:8]}。"
        else:
            raise ValueError("versionControl.action 必须是 inspect 或 commit。")
        return AgUiActionResult(data=data, message=message)

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=VERSION_CONTROL_EVENT_NAME,
        state_key="versionControl",
        run_id_prefix="version-control",
        operation=operation,
        error_message_prefix="版本控制操作失败",
        error_data=lambda _exc: {"action": action},
        accept=accept,
    )


def _version_control_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 AG-UI forwardedProps 中提取版本控制业务输入。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    version_control = forwarded_props.get("versionControl")
    return version_control if isinstance(version_control, dict) else {}
