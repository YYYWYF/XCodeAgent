"""独立应用配置操作的 AG-UI 适配器。"""

from __future__ import annotations

from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import Settings
from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.application_config_orchestrator import ApplicationConfigOrchestrator
from app.services.application_config_orchestrator import ApplicationConfigOperationError

_EVENT = "application-config"


class ApplicationConfigAction(BaseModel):
    """在 AG-UI 边界验证配置动作和预期修订。"""

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    action: Literal["preview", "apply", "get"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    expected_config_revision: int | None = Field(default=None, alias="expectedConfigRevision", ge=1)
    operation_id: str | None = Field(default=None, alias="operationId", min_length=8, max_length=128)
    request_id: str | None = Field(default=None, alias="requestId", min_length=8, max_length=128)
    changes: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "ApplicationConfigAction":
        """要求预览和提交具备 CAS 与明确字段提案。"""

        if self.action in {"preview", "apply"} and (self.expected_config_revision is None or not self.changes):
            raise ValueError("配置预览和提交需要 expectedConfigRevision 与 changes。")
        if self.action in {"apply", "get"} and not self.operation_id:
            raise ValueError("配置提交和读取需要 operationId。")
        return self


def application_config_capabilities() -> dict[str, Any]:
    """发布独立配置操作的 AG-UI 元数据。"""

    return {
        "name": "application-config",
        "endpoint": "/application-config/run",
        "transport": "ag-ui-sse",
        "actions": ["preview", "apply", "get"],
        "customEventName": _EVENT,
        "stateSnapshotKey": "applicationConfigOperation",
        "workflowIndependent": True,
    }


def build_application_config_ag_ui_stream(*, payload: dict[str, Any], settings: Settings, accept: str | None = None) -> AsyncIterator[str]:
    """执行一次配置预览、提交或持久状态读取。"""

    forwarded = payload.get("forwardedProps")
    raw = forwarded.get("applicationConfig") if isinstance(forwarded, dict) else None
    service = ApplicationConfigOrchestrator(settings)

    async def operation() -> AgUiActionResult:
        """把服务结果转换为无敏感值的结构化 AG-UI 结果。"""

        try:
            action_input = ApplicationConfigAction.model_validate(raw)
        except Exception as exc:
            raise ApplicationConfigOperationError("CONFIG_REQUEST_INVALID", "配置请求字段无效。") from exc
        if action_input.action == "preview":
            data = service.preview(action_input.workspace_root, expected_revision=action_input.expected_config_revision, changes=action_input.changes)
        elif action_input.action == "apply":
            data = await service.apply(action_input.workspace_root, operation_id=action_input.operation_id, expected_revision=action_input.expected_config_revision, changes=action_input.changes, request_id=action_input.request_id)
        else:
            data = service.get_operation(action_input.workspace_root, action_input.operation_id)
        return AgUiActionResult(data={"action": action_input.action, "operation": data}, message="应用配置操作已完成。")

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=_EVENT,
        state_key="applicationConfigOperation",
        run_id_prefix="application-config",
        operation=operation,
        error_message_prefix="应用配置操作失败",
        error_data=lambda _exc: {"action": raw.get("action") if isinstance(raw, dict) else None},
        accept=accept,
    )
