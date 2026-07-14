from __future__ import annotations

from typing import Any, AsyncIterator

from app.protocols.ag_ui_action_stream import AgUiActionResult, build_ag_ui_action_stream
from app.services.agent_file_documents import (
    GetAgentFileRequest,
    SaveAgentFileRequest,
    agent_files_root_label,
    read_agents_document,
    save_agents_document,
)


AGENT_FILES_EVENT_NAME = "agent-files"


def agent_files_capabilities() -> dict[str, Any]:
    return {
        "name": "agent-files",
        "endpoint": "/agent-files/run",
        "transport": "ag-ui-sse",
        "actions": ["get", "save"],
        "customEventName": AGENT_FILES_EVENT_NAME,
        "stateSnapshotKey": "agentFiles",
        "root": agent_files_root_label(),
        "workflowIndependent": True,
    }


def build_agent_files_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    agent_file_input = _agent_files_input(payload)
    action = agent_file_input.get("action")

    async def operation() -> AgUiActionResult:
        if action == "get":
            GetAgentFileRequest.model_validate(agent_file_input)
            document = read_agents_document()
            message = "已读取 AGENTS.md。"
        elif action == "save":
            request = SaveAgentFileRequest.model_validate(agent_file_input)
            document = save_agents_document(request.content, request.expected_revision)
            message = "已保存 AGENTS.md。"
        else:
            raise ValueError("agentFiles.action 必须是 get 或 save。")

        return AgUiActionResult(
            data={
                "action": action,
                "root": agent_files_root_label(),
                "document": document.model_dump(by_alias=True),
            },
            message=message,
        )

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=AGENT_FILES_EVENT_NAME,
        state_key="agentFiles",
        run_id_prefix="agent-files",
        operation=operation,
        error_message_prefix="文件操作失败",
        error_data=lambda _exc: {"action": action},
        accept=accept,
    )


def _agent_files_input(payload: dict[str, Any]) -> dict[str, Any]:
    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    agent_files = forwarded_props.get("agentFiles")
    return agent_files if isinstance(agent_files, dict) else {}
