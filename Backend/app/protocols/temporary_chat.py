"""临时对话只读 AG-UI 协议适配器。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.direct_modification import answer_workspace_question
from app.agents.workspace_scope import resolve_workspace_root
from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream


TEMPORARY_CHAT_EVENT_NAME = "temporary-chat"
TEMPORARY_CHAT_STATE_KEY = "temporaryChat"


class TemporaryChatInput(BaseModel):
    """校验临时对话的工作区、当前问题和短期上下文。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    request: str = Field(min_length=1, max_length=16_000)
    conversation_summary: str = Field(
        default="",
        alias="conversationSummary",
        max_length=4_000,
    )

    @field_validator("request")
    @classmethod
    def validate_request(cls, value: str) -> str:
        """去除问题首尾空白并拒绝空消息。"""

        normalized = value.strip()
        if not normalized:
            raise ValueError("临时对话消息不能为空。")
        return normalized


def temporary_chat_capabilities() -> dict[str, Any]:
    """发布临时对话 AG-UI 入口及其只读能力边界。"""

    return {
        "name": "temporary-chat",
        "endpoint": "/temporary-chat/run",
        "transport": "ag-ui-sse",
        "actionField": "forwardedProps.temporaryChat",
        "customEventName": TEMPORARY_CHAT_EVENT_NAME,
        "stateSnapshotKey": TEMPORARY_CHAT_STATE_KEY,
        "workflowIndependent": True,
        "workspaceReadOnly": True,
        "workspaceWrites": False,
        "persistence": "renderer-memory-only",
    }


def build_temporary_chat_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """通过独立 AG-UI 流调用只读 Workspace Assistant 回答一条消息。"""

    raw_input = _temporary_chat_input(payload)

    async def operation() -> AgUiActionResult:
        """校验工作区后在线程池中执行只读问答。"""

        request = TemporaryChatInput.model_validate(raw_input)
        workspace = resolve_workspace_root(request.workspace_root)
        if workspace is None:
            raise ValueError("临时对话必须提供有效的 workspaceRoot。")
        answer = await asyncio.to_thread(
            answer_workspace_question,
            user_request=request.request,
            conversation_summary=request.conversation_summary,
            workspace=str(workspace),
            selected_skill_names=None,
        )
        if not answer.strip():
            raise ValueError("Workspace Assistant 没有返回有效回答。")
        return AgUiActionResult(
            data={
                "schemaVersion": 1,
                "status": "completed",
                "answer": answer,
            },
            message=answer,
        )

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=TEMPORARY_CHAT_EVENT_NAME,
        state_key=TEMPORARY_CHAT_STATE_KEY,
        run_id_prefix="temporary-chat",
        operation=operation,
        error_message_prefix="临时对话失败",
        error_data=lambda _exc: {},
        accept=accept,
        # 临时问答不登记工作区执行，也不写入预览交互或工作流状态。
        register_workspace_run=False,
    )


def _temporary_chat_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 AG-UI forwardedProps 提取临时对话动作参数。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    value = forwarded_props.get("temporaryChat")
    return value if isinstance(value, dict) else {}
