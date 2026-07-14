from collections.abc import Callable
from typing import Any, TypeVar

from app.workspace.code_changes import (
    CapturedWorkspaceChanges,
    capture_workspace_changes,
)

T = TypeVar("T")


def workspace_from_state(state: dict[str, Any]) -> str | None:
    return state.get("workspace") or state.get("workspace_path")


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
