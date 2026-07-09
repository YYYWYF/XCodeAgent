from __future__ import annotations

from pathlib import Path
from typing import Literal

from deepagents.backends import FilesystemBackend, StateBackend
from deepagents.middleware.permissions import FilesystemPermission

from app.workspace.workspace import SENSITIVE_FILE_NAMES


AgentWorkspaceMode = Literal["main", "frontend", "data_source", "test"]


def resolve_workspace_root(workspace_root: str | None) -> Path | None:
    if not workspace_root:
        return None

    root = Path(workspace_root).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"Workspace root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Workspace root is not a directory: {root}")
    return root


def create_workspace_backend(workspace_root: str | None):
    root = resolve_workspace_root(workspace_root)
    if root is None:
        return StateBackend()
    return FilesystemBackend(root_dir=root, virtual_mode=True)


def create_workspace_permissions(
    workspace_root: str | None,
    *,
    mode: AgentWorkspaceMode,
) -> list[FilesystemPermission]:
    if resolve_workspace_root(workspace_root) is None:
        return [
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            )
        ]

    permissions = [
        FilesystemPermission(
            operations=["read", "write"],
            paths=_sensitive_virtual_paths(),
            mode="deny",
        )
    ]
    if mode == "test":
        permissions.extend(
            [
                FilesystemPermission(operations=["read"], paths=["/**"], mode="allow"),
                FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
            ]
        )
        return permissions

    permissions.append(
        FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="allow")
    )
    return permissions


def _sensitive_virtual_paths() -> list[str]:
    paths: list[str] = []
    for name in sorted(SENSITIVE_FILE_NAMES):
        paths.extend([f"/{name}", f"/**/{name}"])
    return paths
