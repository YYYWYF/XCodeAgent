"""从开发阶段最终代码变更中固定 Diff 审查文件清单。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from app.workspace.workspace import _is_sensitive_path


def reviewable_file_path(value: Any) -> str:
    """仅接受全量审查原有边界内的安全相对文件路径。"""

    from app.agents.code_analyze.scope import is_code_review_change_path

    path = str(value or "").strip().replace("\\", "/")
    parsed = PurePosixPath(path)
    if not path or parsed.is_absolute() or ".." in parsed.parts or parsed.as_posix() != path:
        return ""
    if not is_code_review_change_path(path):
        return ""
    if path.startswith("backend/") and not path.endswith(".java"):
        return ""
    return path


def development_review_files(change_set: Any, workspace: str | Path) -> list[str]:
    """按最终变更状态去重，并排除删除、二进制与不可读取的文件。"""

    if not isinstance(change_set, dict) or not isinstance(change_set.get("files"), list):
        return []
    root = Path(workspace).resolve()
    if str(change_set.get("workspaceRoot") or "") != str(root):
        return []
    latest: dict[str, dict[str, Any]] = {}
    for item in change_set["files"]:
        if isinstance(item, dict):
            path = reviewable_file_path(item.get("path"))
            if path:
                latest[path] = item
    return [
        path
        for path, item in sorted(latest.items())
        if item.get("changeType") != "deleted"
        and item.get("binary") is not True
        and current_review_file(root, path)
    ]


def current_review_file(workspace: str | Path, value: Any) -> bool:
    """审查启动时再次校验文件仍处于工作区且可安全读取。"""

    path = reviewable_file_path(value)
    if not path:
        return False
    root = Path(workspace).resolve()
    candidate = root / path
    if any(
        (root.joinpath(*PurePosixPath(path).parts[:index])).is_symlink()
        for index in range(1, len(PurePosixPath(path).parts) + 1)
    ):
        return False
    return (
        candidate.is_file()
        and not candidate.is_symlink()
        and candidate.resolve().is_relative_to(root)
        and not _is_sensitive_path(candidate)
    )
