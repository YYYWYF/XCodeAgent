"""代码审查 Agent 的只读工作区边界。"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileInfo,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)


_CODE_ROOTS = ("frontend/src", "backend/src/main/java")
_LIST_ROOTS = _CODE_ROOTS
_SKILL_FILES = {
    ".xcodeagent/builtin-skills/frontend-code-scan/SKILL.md",
    ".xcodeagent/builtin-skills/backend-code-scan/SKILL.md",
    ".xcodeagent/builtin-skills/backend-code-scan/references/rules-reference.md",
}
_SKILL_LIST_ROOTS = {
    ".xcodeagent/builtin-skills/frontend-code-scan",
    ".xcodeagent/builtin-skills/backend-code-scan",
}


class CodeAnalyzeScopedBackend(BackendProtocol):
    """在默认文件后端外增加代码审查的精确读边界。"""

    def __init__(self, delegate: BackendProtocol):
        """保存默认后端，并在每个文件工具入口执行审查范围校验。"""

        self._delegate = delegate

    def ls(self, path: str) -> LsResult:
        """仅允许浏览源码目录及内置 Skill 目录。"""

        return self._delegate.ls(path) if _is_list_path(path) else LsResult(error=_denied(path))

    async def als(self, path: str) -> LsResult:
        """异步仅允许浏览源码目录及内置 Skill 目录。"""

        return await self._delegate.als(path) if _is_list_path(path) else LsResult(error=_denied(path))

    def ls_info(self, path: str) -> list[FileInfo]:
        """仅允许读取源码和内置 Skill 目录的元数据。"""

        return self._delegate.ls_info(path) if _is_list_path(path) else []

    async def als_info(self, path: str) -> list[FileInfo]:
        """异步仅允许读取源码和内置 Skill 目录的元数据。"""

        return await self._delegate.als_info(path) if _is_list_path(path) else []

    def read(self, file_path: str, offset: int = 0, limit: int = 2_000) -> ReadResult:
        """仅允许读取两端源码和三个必需 Skill 文件。"""

        return (
            self._delegate.read(file_path, offset, limit)
            if _is_read_path(file_path)
            else ReadResult(error=_denied(file_path))
        )

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2_000) -> ReadResult:
        """异步仅允许读取两端源码和三个必需 Skill 文件。"""

        return (
            await self._delegate.aread(file_path, offset, limit)
            if _is_read_path(file_path)
            else ReadResult(error=_denied(file_path))
        )

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        """禁止无路径全工作区搜索，只允许在两个源码根目录内搜索。"""

        return (
            self._delegate.grep(pattern, path, glob)
            if _is_code_path(path)
            else GrepResult(error=_denied(path or "workspace"))
        )

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        """异步禁止无路径全工作区搜索，只允许在两个源码根目录内搜索。"""

        return (
            await self._delegate.agrep(pattern, path, glob)
            if _is_code_path(path)
            else GrepResult(error=_denied(path or "workspace"))
        )

    def grep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> list[Any] | str:
        """禁止无路径原始搜索，只允许在两个源码根目录内搜索。"""

        return self._delegate.grep_raw(pattern, path, glob) if _is_code_path(path) else _denied(path)

    async def agrep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> list[Any] | str:
        """异步禁止无路径原始搜索，只允许在两个源码根目录内搜索。"""

        return (
            await self._delegate.agrep_raw(pattern, path, glob)
            if _is_code_path(path)
            else _denied(path)
        )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """禁止无路径全工作区匹配，只允许在两个源码根目录内匹配。"""

        return (
            self._delegate.glob(pattern, path)
            if _is_code_path(path) or (path is None and _is_code_pattern(pattern))
            else GlobResult(error=_denied(path or "workspace"))
        )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """异步禁止无路径全工作区匹配，只允许在两个源码根目录内匹配。"""

        return (
            await self._delegate.aglob(pattern, path)
            if _is_code_path(path) or (path is None and _is_code_pattern(pattern))
            else GlobResult(error=_denied(path or "workspace"))
        )

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """仅允许在两个源码根目录内读取匹配元数据。"""

        return (
            self._delegate.glob_info(pattern, path)
            if _is_code_path(path) or (path == "/" and _is_code_pattern(pattern))
            else []
        )

    async def aglob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """异步仅允许在两个源码根目录内读取匹配元数据。"""

        return (
            await self._delegate.aglob_info(pattern, path)
            if _is_code_path(path) or (path == "/" and _is_code_pattern(pattern))
            else []
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        """拒绝代码审查 Agent 的所有写入。"""

        return WriteResult(error="code_analyze_write_denied")

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """异步拒绝代码审查 Agent 的所有写入。"""

        return WriteResult(error="code_analyze_write_denied")

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """拒绝代码审查 Agent 的所有编辑。"""

        return EditResult(error="code_analyze_edit_denied")

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """异步拒绝代码审查 Agent 的所有编辑。"""

        return EditResult(error="code_analyze_edit_denied")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """拒绝批量写入。"""

        return [FileUploadResponse(path=path, error="code_analyze_upload_denied") for path, _ in files]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """异步拒绝批量写入。"""

        return [FileUploadResponse(path=path, error="code_analyze_upload_denied") for path, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """仅允许批量读取两个源码目录或必需 Skill 文件。"""

        if any(not _is_read_path(path) for path in paths):
            return [
                FileDownloadResponse(
                    path=path,
                    error=None if _is_read_path(path) else _denied(path),
                )
                for path in paths
            ]
        return self._delegate.download_files(paths)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """异步仅允许批量读取两个源码目录或必需 Skill 文件。"""

        if any(not _is_read_path(path) for path in paths):
            return [
                FileDownloadResponse(
                    path=path,
                    error=None if _is_read_path(path) else _denied(path),
                )
                for path in paths
            ]
        return await self._delegate.adownload_files(paths)

    def __getattr__(self, name: str) -> Any:
        """转发不涉及文件读写的后端能力。"""

        return getattr(self._delegate, name)


def normalize_virtual_path(value: Any) -> str:
    """规范化 Agent 虚拟路径并拒绝目录穿越。"""

    normalized = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not normalized or ".." in PurePosixPath(normalized).parts:
        return ""
    return normalized.rstrip("/")


def is_code_analyze_read_path(value: Any) -> bool:
    """判断路径是否属于审查允许读取范围。"""

    path = normalize_virtual_path(value)
    return _is_read_path(path)


def _is_code_path(value: Any) -> bool:
    """判断路径是否落在两个源码根目录内。"""

    path = normalize_virtual_path(value)
    return bool(path) and any(path == root or path.startswith(f"{root}/") for root in _CODE_ROOTS)


def _is_code_pattern(value: Any) -> bool:
    """判断不带 base path 的 glob 模式是否显式锚定到源码目录。"""

    pattern = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not pattern or ".." in PurePosixPath(pattern).parts:
        return False
    return any(pattern.startswith(f"{root}/") for root in _CODE_ROOTS)


def _is_read_path(value: Any) -> bool:
    """判断路径是否属于源码目录或固定内置 Skill 文件。"""

    path = normalize_virtual_path(value)
    return _is_code_path(path) or path in _SKILL_FILES


def _is_list_path(value: Any) -> bool:
    """判断目录列表请求是否只触及允许的目录元数据。"""

    path = normalize_virtual_path(value)
    return path in _LIST_ROOTS or path in _SKILL_LIST_ROOTS


def _denied(value: Any) -> str:
    """生成不暴露宿主绝对路径的拒绝原因。"""

    return f"code_analyze_path_denied: {normalize_virtual_path(value) or 'invalid_path'}"
