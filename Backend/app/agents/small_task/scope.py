"""SmallTask 文件工具的动态授权范围。"""

from __future__ import annotations

import fnmatch
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)


_CURRENT_SMALL_TASK_PATHS: ContextVar[tuple[str, ...]] = ContextVar(
    "xcodeagent_small_task_paths",
    default=(),
)
_FORMAL_PATH_MARKERS = (".xcodeagent/", "requirement-spec", "project-plan", "build-task")
_IGNORED_CODE_CONTEXT_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".next",
        ".svn",
        ".turbo",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "target",
    }
)


@contextmanager
def small_task_path_scope(paths: list[str] | tuple[str, ...]) -> Iterator[None]:
    """在一次 Agent 调用的 Context 中安装不可跨线程污染的文件写入范围。"""

    token = _CURRENT_SMALL_TASK_PATHS.set(
        tuple(
            normalized
            for path in paths
            if (normalized := _normalize_scope(path))
        )
    )
    try:
        yield
    finally:
        _CURRENT_SMALL_TASK_PATHS.reset(token)


def is_small_task_path_allowed(path: str) -> bool:
    """判断虚拟工作区路径是否命中当前 SmallTask 的授权范围。"""

    candidate = _normalize_candidate(path)
    if not candidate or _is_forbidden_path(candidate):
        return False
    return any(_matches_scope(candidate, scope) for scope in _CURRENT_SMALL_TASK_PATHS.get())


class ScopedSmallTaskBackend(BackendProtocol):
    """给底层 workspace backend 加上按任务动态收紧的写入边界。"""

    def __init__(self, delegate: BackendProtocol):
        self._delegate = delegate

    def ls(self, path: str) -> LsResult:
        """列出源码目录并过滤安装依赖、缓存和构建产物。"""

        if _is_ignored_code_context_path(path):
            return LsResult(error=_read_scope_error(path), entries=[])
        result = self._delegate.ls(path)
        entries = result.entries
        if not isinstance(entries, list):
            return result
        return LsResult(
            error=result.error,
            entries=[
                entry
                for entry in entries
                if not _is_ignored_code_context_path(str(entry.get("path") or ""))
            ],
        )

    def read(self, file_path: str, offset: int = 0, limit: int = 2_000) -> ReadResult:
        """读取源码文件并拒绝依赖安装目录和生成目录。"""

        if _is_ignored_code_context_path(file_path):
            return ReadResult(error=_read_scope_error(file_path))
        return self._delegate.read(file_path, offset, limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        """默认从当前任务的源码根搜索，并过滤生成或依赖目录。"""

        if _is_ignored_code_context_path(path or "") or _is_ignored_code_context_path(
            glob or ""
        ):
            return GrepResult(error=_read_scope_error(path or glob or ""), matches=[])
        result = self._delegate.grep(pattern, path or _preferred_read_root(), glob)
        matches = result.matches
        if not isinstance(matches, list):
            return result
        return GrepResult(
            error=result.error,
            matches=[
                match
                for match in matches
                if not _is_ignored_code_context_path(str(match.get("path") or ""))
            ],
        )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """默认从当前任务的源码根匹配，并拒绝依赖和构建目录。"""

        if _is_ignored_code_context_path(pattern) or _is_ignored_code_context_path(
            path or ""
        ):
            return GlobResult(error=_read_scope_error(path or pattern), matches=[])
        result = self._delegate.glob(pattern, path or _preferred_read_root())
        matches = result.matches
        if not isinstance(matches, list):
            return result
        return GlobResult(
            error=result.error,
            matches=[
                match
                for match in matches
                if not _is_ignored_code_context_path(str(match.get("path") or ""))
            ],
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        """只允许创建授权范围内的文件，拒绝无范围或越界写入。"""

        if not is_small_task_path_allowed(file_path):
            return WriteResult(error=_scope_error(file_path))
        return self._delegate.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """只允许修改授权范围内的文件，避免模型扩大任务边界。"""

        if not is_small_task_path_allowed(file_path):
            return EditResult(error=_scope_error(file_path))
        return self._delegate.edit(file_path, old_string, new_string, replace_all)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """转发批量源码读取，同时拒绝依赖安装目录和生成目录。"""

        allowed_paths = [
            path for path in paths if not _is_ignored_code_context_path(path)
        ]
        allowed_responses = iter(self._delegate.download_files(allowed_paths))
        return [
            (
                FileDownloadResponse(path=path, error="permission_denied")
                if _is_ignored_code_context_path(path)
                else next(allowed_responses)
            )
            for path in paths
        ]

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """按当前任务范围校验批量写入，再转发给底层工作区 backend。"""

        denied = {
            path
            for path, _content in files
            if not is_small_task_path_allowed(path)
        }
        if denied:
            return [
                FileUploadResponse(
                    path=path,
                    error=_scope_error(path) if path in denied else None,
                )
                for path, _content in files
            ]
        return self._delegate.upload_files(files)

    def __getattr__(self, name: str) -> Any:
        """把未涉及写入边界的兼容 backend 属性交给底层实现。"""

        return getattr(self._delegate, name)


def _normalize_scope(value: Any) -> str:
    """规范化任务声明的相对路径或 glob。"""

    normalized = str(value or "").strip().replace("\\", "/").lstrip("/").rstrip("/")
    if not normalized or ".." in normalized.split("/") or _is_forbidden_path(normalized):
        return ""
    return normalized


def _normalize_candidate(value: Any) -> str:
    """规范化 backend 传入的虚拟绝对路径。"""

    return str(value or "").strip().replace("\\", "/").lstrip("/").rstrip("/")


def _is_forbidden_path(path: str) -> bool:
    """拒绝敏感环境文件和工作流正式产物目录。"""

    lower = path.casefold()
    parts = lower.split("/")
    return (
        any(part == ".env" or part.startswith(".env.") for part in parts)
        or any(marker in lower for marker in _FORMAL_PATH_MARKERS)
        or any(part in _IGNORED_CODE_CONTEXT_DIRS for part in parts)
    )


def _is_ignored_code_context_path(path: str) -> bool:
    """判断路径或 glob 是否进入安装依赖、缓存或生成目录。"""

    normalized = _normalize_candidate(path).casefold()
    return any(part in _IGNORED_CODE_CONTEXT_DIRS for part in normalized.split("/"))


def _preferred_read_root() -> str:
    """从当前写入范围推导默认源码搜索根，避免无路径搜索遍历整个工程。"""

    for scope in _CURRENT_SMALL_TASK_PATHS.get():
        parts: list[str] = []
        for part in scope.split("/"):
            if any(token in part for token in ("*", "?", "[")):
                break
            if part:
                parts.append(part)
        if not parts:
            continue
        candidate = "/".join(parts)
        if "." in parts[-1]:
            candidate = "/".join(parts[:-1])
        if candidate:
            return f"/{candidate}"
    return "/"


def _matches_scope(candidate: str, scope: str) -> bool:
    """匹配精确文件、目录范围和 glob 范围。"""

    if fnmatch.fnmatchcase(candidate, scope):
        return True
    return candidate == scope or candidate.startswith(f"{scope}/")


def _scope_error(path: str) -> str:
    """生成不暴露宿主路径的越界写入错误。"""

    return f"SmallTask Agent 无权修改该路径：{_normalize_candidate(path) or 'unknown'}。"


def _read_scope_error(path: str) -> str:
    """生成依赖和构建目录不可读的稳定错误。"""

    return (
        "SmallTask Agent 不读取安装依赖、缓存或构建产物目录："
        f"{_normalize_candidate(path) or 'unknown'}。请先检查项目源码。"
    )
