from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any, TypeVar

from app.agents.workspace_scope import resolve_workspace_root
from app.workspace.code_changes import (
    CapturedWorkspaceChanges,
    capture_workspace_changes,
)

T = TypeVar("T")


def workspace_from_state(state: dict[str, Any]) -> str | None:
    """读取节点状态中由协议边界传入的用户 workspaceRoot。"""

    return state.get("workspace") or state.get("workspace_path")


def capture_agent_file_changes(
    *,
    workspace: str | None,
    source_tool: str,
    action: Callable[[], T],
    capture_exceptions: bool = False,
    ignored_dirs: set[str] | None = None,
    included_roots: tuple[str, ...] | None = None,
) -> CapturedWorkspaceChanges:
    """执行 Agent 并按调用方策略捕获工作区差异和异常。"""

    return capture_workspace_changes(
        workspace=workspace,
        source_tool=source_tool,
        action=action,
        capture_exceptions=capture_exceptions,
        ignored_dirs=ignored_dirs,
        included_roots=included_roots,
    )


def refresh_code_graph_after_changes(
    workspace: str | None,
    change_sets: list[dict[str, Any]] | None,
    *,
    on_progress: Callable[[Any], None] | None = None,
) -> dict[str, Any] | None:
    """把本批 Agent 实际写入的相对路径提交给用户工作区代码图增量更新。"""

    if not workspace or not change_sets:
        return None
    changed_files = sorted(
        {
            normalized
            for change_set in change_sets
            if isinstance(change_set, dict)
            for item in (change_set.get("files") or [])
            if isinstance(item, dict)
            for normalized in [_safe_relative_change_path(item.get("path"))]
            if normalized
        }
    )
    if not changed_files:
        return None
    try:
        from app.services.code_graph.manager import get_code_graph_manager

        resolved_workspace = resolve_workspace_root(workspace)
        if resolved_workspace is None:
            return None
        result = get_code_graph_manager().update_paths(
            resolved_workspace,
            changed_files,
            callback=on_progress,
        )
        return result.as_dict()
    except (OSError, RuntimeError, ValueError):
        # 图索引只是导航优化，更新失败不应改变已经完成的代码阶段结果。
        return None


def _safe_relative_change_path(value: Any) -> str:
    """只接受 workspace-relative 变更路径，拒绝绝对路径和目录穿越。"""

    normalized = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or (len(normalized) >= 2 and normalized[1] == ":")
        or ".." in path.parts
    ):
        return ""
    return path.as_posix()
