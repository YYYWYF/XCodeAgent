from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from app.workspace.workspace import (
    CODE_CHANGE_DIFF_LIMIT,
    _diff_stats,
    _is_sensitive_path,
    _looks_binary,
    _relative_path,
    _should_ignore,
    _text_diff,
    _truncate,
    _workspace_root,
)

MAX_SNAPSHOT_FILE_BYTES = 1_000_000
T = TypeVar("T")


@dataclass(frozen=True)
class WorkspaceFileSnapshot:
    path: str
    sha256: str
    binary: bool
    content: str | None


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: Path
    files: dict[str, WorkspaceFileSnapshot]


@dataclass(frozen=True)
class CapturedWorkspaceChanges:
    value: Any
    code_change_set: dict[str, Any] | None


def snapshot_workspace(workspace_root: str | None) -> WorkspaceSnapshot | None:
    """Return a text-safe snapshot of files inside workspace_root."""

    if not workspace_root:
        return None

    root = _workspace_root(workspace_root)
    files: dict[str, WorkspaceFileSnapshot] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if not _should_ignore(current / name, root, include_hidden=True)
        ]

        for filename in sorted(filenames):
            path = current / filename
            if _skip_snapshot_path(path, root):
                continue

            try:
                raw = path.read_bytes()
            except OSError:
                continue

            rel = _relative_path(path, root)
            binary = _looks_binary(raw) or len(raw) > MAX_SNAPSHOT_FILE_BYTES
            files[rel] = WorkspaceFileSnapshot(
                path=rel,
                sha256=hashlib.sha256(raw).hexdigest(),
                binary=binary,
                content=None if binary else raw.decode("utf-8", errors="replace"),
            )

    return WorkspaceSnapshot(root=root, files=files)


def diff_workspace_snapshots(
    before: WorkspaceSnapshot | None,
    after: WorkspaceSnapshot | None,
    *,
    source_tool: str,
) -> list[dict[str, Any]]:
    if before is None or after is None:
        return []
    if before.root != after.root:
        return []

    files: list[dict[str, Any]] = []
    for rel in sorted(set(before.files) | set(after.files)):
        before_file = before.files.get(rel)
        after_file = after.files.get(rel)
        if before_file and after_file and before_file.sha256 == after_file.sha256:
            continue
        files.append(
            _code_change_payload_from_snapshots(
                root=before.root,
                path=rel,
                before_file=before_file,
                after_file=after_file,
                source_tool=source_tool,
            )
        )
    return files


def build_code_change_set(
    *,
    workspace_root: str | Path,
    files: list[dict[str, Any]],
    source_tool: str,
) -> dict[str, Any] | None:
    if not files:
        return None

    root = _workspace_root(str(workspace_root))
    digest_source = json.dumps(
        {
            "workspaceRoot": str(root),
            "sourceTool": source_tool,
            "files": [
                {
                    "id": item.get("id"),
                    "path": item.get("path"),
                    "changeType": item.get("changeType"),
                }
                for item in files
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"code-change-set:{digest}",
        "status": "applied",
        "workspaceRoot": str(root),
        "summary": {
            "files": len({str(item.get("path")) for item in files if item.get("path")}),
            "additions": sum(_safe_int(item.get("additions")) for item in files),
            "deletions": sum(_safe_int(item.get("deletions")) for item in files),
        },
        "files": files,
    }


def capture_workspace_changes(
    *,
    workspace: str | None,
    source_tool: str,
    action: Callable[[], T],
) -> CapturedWorkspaceChanges:
    before = snapshot_workspace(workspace)
    value = action()
    after = snapshot_workspace(workspace)
    files = diff_workspace_snapshots(before, after, source_tool=source_tool)
    code_change_set = (
        build_code_change_set(
            workspace_root=after.root if after is not None else workspace or "",
            files=files,
            source_tool=source_tool,
        )
        if files and after is not None
        else None
    )
    return CapturedWorkspaceChanges(value=value, code_change_set=code_change_set)


def code_change_state_update(
    code_change_set: dict[str, Any] | None,
) -> dict[str, Any]:
    if not code_change_set:
        return {}
    return {
        "code_changes": code_change_set,
        "code_change_sets": [code_change_set],
    }


def merge_code_change_sets(
    code_change_sets: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    sets = [item for item in code_change_sets if _valid_change_set(item)]
    if not sets:
        return None

    files = [
        dict(file_item)
        for change_set in sets
        for file_item in change_set.get("files", [])
        if isinstance(file_item, dict)
    ]
    if not files:
        return None

    workspace_root = str(sets[-1].get("workspaceRoot") or sets[0].get("workspaceRoot") or "")
    digest_source = json.dumps(
        {
            "workspaceRoot": workspace_root,
            "sets": [item.get("id") for item in sets],
            "files": [
                {
                    "id": item.get("id"),
                    "path": item.get("path"),
                    "changeType": item.get("changeType"),
                }
                for item in files
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"code-change-set:{digest}",
        "status": "applied",
        "workspaceRoot": workspace_root,
        "summary": {
            "files": len({str(item.get("path")) for item in files if item.get("path")}),
            "additions": sum(_safe_int(item.get("additions")) for item in files),
            "deletions": sum(_safe_int(item.get("deletions")) for item in files),
        },
        "files": files,
    }


def _skip_snapshot_path(path: Path, root: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return True
    if _should_ignore(path, root, include_hidden=True):
        return True
    return _is_sensitive_path(path)


def _code_change_payload_from_snapshots(
    *,
    root: Path,
    path: str,
    before_file: WorkspaceFileSnapshot | None,
    after_file: WorkspaceFileSnapshot | None,
    source_tool: str,
) -> dict[str, Any]:
    if before_file is None:
        change_type = "added"
        tool = "file.write"
    elif after_file is None:
        change_type = "deleted"
        tool = "file.delete"
    else:
        change_type = "modified"
        tool = "file.patch"

    before_content = before_file.content if before_file is not None else ""
    after_content = after_file.content if after_file is not None else ""
    binary = (
        (before_file.binary if before_file is not None else False)
        or (after_file.binary if after_file is not None else False)
        or before_content is None
        or after_content is None
    )
    diff = (
        ""
        if binary
        else _text_diff(
            before_content or "",
            after_content or "",
            fromfile=path,
            tofile=path,
        )
    )
    stats = _diff_stats(diff)
    digest = hashlib.sha256(
        f"{source_tool}:{tool}:{path}:{change_type}:{diff}".encode("utf-8")
    ).hexdigest()[:16]
    return {
        "id": f"{tool}:{path}:{digest}",
        "path": path,
        "changeType": change_type,
        "additions": stats["additions"],
        "deletions": stats["deletions"],
        "diff": _truncate(diff, CODE_CHANGE_DIFF_LIMIT),
        "truncated": len(diff) > CODE_CHANGE_DIFF_LIMIT,
        "binary": binary,
        "tool": tool,
        "sourceTool": source_tool,
        "executed": True,
        "workspaceRoot": str(root),
    }


def _valid_change_set(value: dict[str, Any]) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("files"), list)
        and isinstance(value.get("summary"), dict)
    )


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
