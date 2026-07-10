from __future__ import annotations

from pathlib import Path


VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS = (
    "The filesystem backend is already rooted at the selected workspaceRoot. "
    "Use only virtual absolute paths starting with '/', such as "
    "'/app/frontend/App.tsx'. Never include, repeat, or reconstruct the host "
    "workspaceRoot path in a filesystem tool call."
)


def host_workspace_virtual_alias(root: Path) -> str | None:
    """Return the virtual path that would incorrectly repeat the host root."""

    resolved = root.resolve()
    alias = "/" + resolved.as_posix().lstrip("/")
    return None if alias == "/" else alias.rstrip("/")


def host_workspace_virtual_deny_patterns(root: Path) -> list[str]:
    alias = host_workspace_virtual_alias(root)
    return [alias, f"{alias}/**"] if alias else []


def is_host_workspace_virtual_path(root: Path, virtual_path: str) -> bool:
    alias = host_workspace_virtual_alias(root)
    if not alias:
        return False

    normalized = "/" + str(virtual_path).replace("\\", "/").lstrip("/")
    normalized = normalized.rstrip("/") or "/"
    return normalized == alias or normalized.startswith(f"{alias}/")
