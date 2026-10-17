"""应用公开配置投影的独立 AG-UI 动作协议。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.application_config.projection import project_workspace_app_config

APPLICATION_CONFIG_PROJECTION_EVENT_NAME = "application-config-projection"


class ApplicationConfigProjectionAction(BaseModel):
    """校验设置保存后允许执行的唯一投影动作。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    action: Literal["project"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    expected_config_revision: int = Field(alias="expectedConfigRevision", ge=1)


def application_config_projection_capabilities() -> dict[str, Any]:
    """发布应用配置投影的独立 AG-UI 能力。"""

    return {
        "name": "application-config-projection",
        "endpoint": "/application-config/run",
        "transport": "ag-ui-sse",
        "actionField": "forwardedProps.applicationConfigProjection",
        "actions": ["project"],
        "customEventName": APPLICATION_CONFIG_PROJECTION_EVENT_NAME,
        "stateSnapshotKey": "applicationConfigProjection",
        "workflowIndependent": True,
    }


def build_application_config_projection_ag_ui_stream(
    *,
    payload: dict[str, Any],
    accept: str | None = None,
) -> AsyncIterator[str]:
    """从 canonical 配置执行受限投影并发送完整 AG-UI 生命周期。"""

    forwarded_props = payload.get("forwardedProps")
    raw_action = (
        forwarded_props.get("applicationConfigProjection")
        if isinstance(forwarded_props, dict)
        else None
    )
    if not isinstance(raw_action, dict):
        raise ValueError("缺少 forwardedProps.applicationConfigProjection。")

    async def operation() -> AgUiActionResult:
        """在工作区互斥边界内执行幂等配置投影。"""

        request = ApplicationConfigProjectionAction.model_validate(raw_action)
        result = await asyncio.to_thread(
            project_workspace_app_config,
            request.workspace_root,
            expected_config_revision=request.expected_config_revision,
        )
        messages = {
            "CHANGED": "应用公开配置已同步到工程。",
            "NO_CHANGE": "应用公开配置无需更新。",
            "SKIPPED_NO_TEMPLATE": "应用模板尚未生成，已保留配置供首次生成时同步。",
        }
        return AgUiActionResult(
            data={"action": request.action, "projection": result.as_dict()},
            message=messages[result.status],
        )

    workspace_root = str(raw_action.get("workspaceRoot") or "") or None
    return build_ag_ui_action_stream(
        payload=payload,
        event_name=APPLICATION_CONFIG_PROJECTION_EVENT_NAME,
        state_key="applicationConfigProjection",
        run_id_prefix="application-config-projection",
        operation=operation,
        error_message_prefix="应用配置同步失败",
        error_data=lambda exc: {
            "action": raw_action.get("action"),
            "code": getattr(exc, "code", "APP_CONFIG_PROJECTION_FAILED"),
        },
        accept=accept,
        workspace_root=workspace_root,
    )
