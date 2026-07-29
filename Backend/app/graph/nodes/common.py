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
    capture_exceptions: bool = False,
) -> CapturedWorkspaceChanges:
    """执行 Agent 并按调用方策略捕获工作区差异和异常。"""

    return capture_workspace_changes(
        workspace=workspace,
        source_tool=source_tool,
        action=action,
        capture_exceptions=capture_exceptions,
    )
