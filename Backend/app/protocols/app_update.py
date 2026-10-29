"""登录前桌面更新的独立 AG-UI 协议，不进入业务工作流或工作区。"""

from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.app_update import check_app_update


class AppUpdateInput(BaseModel):
    """客户端只提交当前版本，模板服务地址始终由 Python 配置决定。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["check"]
    current_version: str = Field(alias="currentVersion", min_length=1)


def app_update_capabilities() -> dict[str, Any]:
    """发布更新协议能力，不向客户端暴露模板服务配置。"""

    return {
        "name": "app-update",
        "endpoint": "/app-update/run",
        "transport": "ag-ui-sse",
        "actionField": "forwardedProps.appUpdate",
        "actions": ["check"],
        "customEventName": "app-update",
        "stateSnapshotKey": "appUpdate",
        "workflowIndependent": True,
    }


def build_app_update_ag_ui_stream(
    *, payload: dict[str, Any], settings: Settings, accept: str | None = None
) -> AsyncIterator[str]:
    """成功和失败都通过标准生命周期返回，不要求登录或业务工作区。"""

    async def operation() -> AgUiActionResult:
        """严格校验动作输入后复用现有 Settings 转发 Java 版本检测。"""

        forwarded = payload.get("forwardedProps")
        raw = forwarded.get("appUpdate") if isinstance(forwarded, dict) else None
        try:
            request = AppUpdateInput.model_validate(raw)
        except ValidationError:
            raise ValueError("appUpdate 必须包含 check 动作和 currentVersion 字符串。") from None
        release = await check_app_update(settings, request.current_version)
        return AgUiActionResult(
            data=release.model_dump(by_alias=True), message="应用版本检测已完成。"
        )

    return build_ag_ui_action_stream(
        payload=payload,
        event_name="app-update",
        state_key="appUpdate",
        run_id_prefix="app-update",
        operation=operation,
        error_message_prefix="版本检测失败",
        accept=accept,
        register_workspace_run=False,
    )
