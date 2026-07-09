from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.workspace.workspace import SENSITIVE_FILE_NAMES


class DeleteFileInput(BaseModel):
    file_path: str = Field(
        description=(
            "Virtual absolute path to the file to delete, rooted at workspaceRoot. "
            "Example: /data.json"
        )
    )


def create_delete_file_tool(workspace_root: str | None):
    @tool("delete_file", args_schema=DeleteFileInput)
    def delete_file(file_path: str) -> str:
        """Delete one regular file inside the selected workspaceRoot."""

        result = _delete_workspace_file(workspace_root, file_path)
        return json.dumps(result, ensure_ascii=False)

    return delete_file


def _delete_workspace_file(
    workspace_root: str | None,
    file_path: str,
) -> dict[str, Any]:
    try:
        root = _resolve_workspace_root(workspace_root)
        virtual_path = _normalize_virtual_path(file_path)
        target = _resolve_virtual_path(root, virtual_path)
        _validate_delete_target(root, target)
        target.unlink()
        return {
            "tool": "delete_file",
            "status": "deleted",
            "path": virtual_path,
        }
    except ValueError as exc:
        return _error_payload(file_path, str(exc))
    except OSError as exc:
        return _error_payload(file_path, f"Failed to delete file: {exc}")


def _resolve_workspace_root(workspace_root: str | None) -> Path:
    if not workspace_root:
        raise ValueError("workspaceRoot is required before deleting files.")

    root = Path(workspace_root).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"workspaceRoot does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"workspaceRoot is not a directory: {root}")
    return root


def _normalize_virtual_path(file_path: str) -> str:
    value = str(file_path or "").strip()
    if not value:
        raise ValueError("file_path is required.")
    if "\\" in value:
        raise ValueError("file_path must use POSIX '/' separators.")
    if not value.startswith("/"):
        raise ValueError("file_path must be a virtual absolute path like /data.json.")

    parts = PurePosixPath(value).parts
    if ".." in parts or any(part.startswith("~") for part in parts):
        raise ValueError("file_path must not contain '..' or '~'.")
    if value == "/":
        raise ValueError("file_path must point to a file, not the workspace root.")
    return value


def _resolve_virtual_path(root: Path, virtual_path: str) -> Path:
    target = root / virtual_path.lstrip("/")
    resolved = target.resolve(strict=False)
    if not _is_relative_to(resolved, root):
        raise ValueError("file_path escapes workspaceRoot.")
    return target


def _validate_delete_target(root: Path, target: Path) -> None:
    if _has_symlink_component(root, target):
        raise ValueError("Refusing to delete symlink paths.")
    if target.name in SENSITIVE_FILE_NAMES:
        raise ValueError(f"Refusing to delete sensitive file: {target.name}")
    if not target.exists():
        raise ValueError(f"File does not exist: {_virtual_display_path(root, target)}")
    if target.is_dir():
        raise ValueError("delete_file only deletes regular files, not directories.")
    if not target.is_file():
        raise ValueError("delete_file only deletes regular files.")


def _has_symlink_component(root: Path, target: Path) -> bool:
    try:
        relative = target.relative_to(root)
    except ValueError:
        return True

    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _virtual_display_path(root: Path, target: Path) -> str:
    try:
        return "/" + target.relative_to(root).as_posix()
    except ValueError:
        return str(target)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _error_payload(file_path: str, message: str) -> dict[str, Any]:
    return {
        "tool": "delete_file",
        "status": "error",
        "path": file_path,
        "error": message,
    }
