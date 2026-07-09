from collections.abc import Callable
from typing import Any, TypeVar

from app.agents import create_agent_bundle
from app.workspace.code_changes import (
    CapturedWorkspaceChanges,
    capture_workspace_changes,
)

T = TypeVar("T")


def workspace_from_state(state: dict[str, Any]) -> str | None:
    return state.get("workspace") or state.get("workspace_path")


def last_agent_text(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    if not messages:
        return "Agent completed without a text message."
    content = getattr(messages[-1], "content", "")
    return content if isinstance(content, str) else str(content)


def run_live(agent_name: str, prompt: str, workspace: str | None = None) -> str:
    agent = getattr(create_agent_bundle(workspace), agent_name)
    result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
    return last_agent_text(result)


def capture_agent_file_changes(
    *,
    workspace: str | None,
    source_tool: str,
    action: Callable[[], T],
) -> CapturedWorkspaceChanges:
    return capture_workspace_changes(
        workspace=workspace,
        source_tool=source_tool,
        action=action,
    )
