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
from app.workspace.workspace import SENSITIVE_FILE_NAMES


_FRONTEND_ROOT = "frontend"
_BACKEND_ROOT = "backend/src/main/java"
_CODE_ROOTS = (_FRONTEND_ROOT, _BACKEND_ROOT)
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

        return _filter_ls_result(self._delegate.ls(path)) if _is_list_path(path) else LsResult(error=_denied(path))

    async def als(self, path: str) -> LsResult:
        """异步仅允许浏览源码目录及内置 Skill 目录。"""

        return _filter_ls_result(await self._delegate.als(path)) if _is_list_path(path) else LsResult(error=_denied(path))

    def ls_info(self, path: str) -> list[FileInfo]:
        """仅允许读取源码和内置 Skill 目录的元数据。"""

        return _filter_file_infos(self._delegate.ls_info(path)) if _is_list_path(path) else []

    async def als_info(self, path: str) -> list[FileInfo]:
        """异步仅允许读取源码和内置 Skill 目录的元数据。"""

        return _filter_file_infos(await self._delegate.als_info(path)) if _is_list_path(path) else []

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
            _filter_grep_result(self._delegate.grep(pattern, path, glob))
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
            _filter_grep_result(await self._delegate.agrep(pattern, path, glob))
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

        return _filter_raw_matches(self._delegate.grep_raw(pattern, path, glob)) if _is_code_path(path) else _denied(path)

    async def agrep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> list[Any] | str:
        """异步禁止无路径原始搜索，只允许在两个源码根目录内搜索。"""

        return (
            _filter_raw_matches(await self._delegate.agrep_raw(pattern, path, glob))
            if _is_code_path(path)
            else _denied(path)
        )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """禁止无路径全工作区匹配，只允许在两个源码根目录内匹配。"""

        return (
            _filter_glob_result(self._delegate.glob(pattern, path))
            if _is_code_path(path) or (path is None and _is_code_pattern(pattern))
            else GlobResult(error=_denied(path or "workspace"))
        )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """异步禁止无路径全工作区匹配，只允许在两个源码根目录内匹配。"""

        return (
            _filter_glob_result(await self._delegate.aglob(pattern, path))
            if _is_code_path(path) or (path is None and _is_code_pattern(pattern))
            else GlobResult(error=_denied(path or "workspace"))
        )

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """仅允许在两个源码根目录内读取匹配元数据。"""

        return (
            _filter_file_infos(self._delegate.glob_info(pattern, path))
            if _is_code_path(path) or (path == "/" and _is_code_pattern(pattern))
            else []
        )

    async def aglob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """异步仅允许在两个源码根目录内读取匹配元数据。"""

        return (
            _filter_file_infos(await self._delegate.aglob_info(pattern, path))
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


class CodeReviewRepairScopedBackend(CodeAnalyzeScopedBackend):
    """在审查读取边界内允许修复 Agent 修改授权项目文件。"""

    def write(self, file_path: str, content: str) -> WriteResult:
        """只允许写入安全前端文件或后端业务源码。"""

        return (
            self._delegate.write(file_path, content)
            if _is_repair_write_path(file_path)
            else WriteResult(error=_denied(file_path))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """异步只允许写入安全前端文件或后端业务源码。"""

        return (
            await self._delegate.awrite(file_path, content)
            if _is_repair_write_path(file_path)
            else WriteResult(error=_denied(file_path))
        )

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """只允许编辑安全前端文件或后端业务源码。"""

        return (
            self._delegate.edit(file_path, old_string, new_string, replace_all)
            if _is_repair_write_path(file_path)
            else EditResult(error=_denied(file_path))
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """异步只允许编辑两个业务源码根目录内的文件。"""

        return (
            await self._delegate.aedit(file_path, old_string, new_string, replace_all)
            if _is_repair_write_path(file_path)
            else EditResult(error=_denied(file_path))
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """只允许批量写入业务源码文件。"""

        if any(not _is_repair_write_path(path) for path, _ in files):
            return [
                FileUploadResponse(
                    path=path,
                    error=None if _is_repair_write_path(path) else _denied(path),
                )
                for path, _ in files
            ]
        return self._delegate.upload_files(files)

    async def aupload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        """异步只允许批量写入业务源码文件。"""

        if any(not _is_repair_write_path(path) for path, _ in files):
            return [
                FileUploadResponse(
                    path=path,
                    error=None if _is_repair_write_path(path) else _denied(path),
                )
                for path, _ in files
            ]
        return await self._delegate.aupload_files(files)


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


def is_code_review_change_path(value: Any) -> bool:
    """判断真实审查修复 Diff 是否位于授权范围，包含 pnpm 生成的锁文件。"""

    path = normalize_virtual_path(value)
    return _is_safe_frontend_path(path) or _is_backend_source_path(path)


def _is_code_path(value: Any) -> bool:
    """判断路径是否落在安全前端范围或后端源码根目录内。"""

    path = normalize_virtual_path(value)
    return _is_safe_frontend_path(path) or _is_backend_source_path(path)


def _is_repair_write_path(value: Any) -> bool:
    """判断修复 Agent 是否可以修改路径，并保留 lockfile 的工具专属写入。"""

    path = normalize_virtual_path(value)
    if _is_safe_frontend_path(path):
        return path != "frontend/pnpm-lock.yaml"
    if not _is_backend_source_path(path):
        return False
    parts = PurePosixPath(path).parts
    if any(part.casefold() in {"test", "tests", "__tests__", "spec", "specs"} for part in parts):
        return False
    filename = parts[-1].casefold() if parts else ""
    return not any(
        filename.endswith(suffix)
        for suffix in (".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx")
    )


def _is_code_pattern(value: Any) -> bool:
    """判断不带 base path 的 glob 模式是否显式锚定到授权目录。"""

    pattern = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not pattern or ".." in PurePosixPath(pattern).parts:
        return False
    return _is_safe_frontend_path(pattern) or _is_backend_source_path(pattern)


def _is_read_path(value: Any) -> bool:
    """判断路径是否属于授权项目目录或固定内置 Skill 文件。"""

    path = normalize_virtual_path(value)
    return _is_code_path(path) or path in _SKILL_FILES


def _is_list_path(value: Any) -> bool:
    """判断目录列表请求是否只触及允许的目录元数据。"""

    path = normalize_virtual_path(value)
    return _is_code_path(path) or path in _SKILL_LIST_ROOTS


def _is_safe_frontend_path(value: Any) -> bool:
    """允许 frontend 全目录，但拒绝依赖目录、敏感文件和穿越路径。"""

    path = normalize_virtual_path(value)
    if not path or not (path == _FRONTEND_ROOT or path.startswith(f"{_FRONTEND_ROOT}/")):
        return False
    parts = PurePosixPath(path).parts
    return not any(part.casefold() == "node_modules" for part in parts) and not any(
        part in SENSITIVE_FILE_NAMES for part in parts
    )


def _is_backend_source_path(value: Any) -> bool:
    """判断路径是否位于固定后端业务源码根。"""

    path = normalize_virtual_path(value)
    return bool(path) and (path == _BACKEND_ROOT or path.startswith(f"{_BACKEND_ROOT}/"))


def _safe_result_path(value: Any) -> bool:
    """过滤委托后端可能递归返回的 node_modules 或敏感路径。"""

    path = normalize_virtual_path(value)
    return not path.startswith("frontend/") or _is_safe_frontend_path(path)


def _filter_file_infos(values: list[FileInfo]) -> list[FileInfo]:
    """从文件元数据列表移除不允许暴露的前端路径。"""

    return [item for item in values if _safe_result_path(item.get("path"))]


def _filter_ls_result(result: LsResult) -> LsResult:
    """过滤目录列表中的依赖目录和敏感文件。"""

    if result.entries is not None:
        result.entries = _filter_file_infos(result.entries)
    return result


def _filter_grep_result(result: GrepResult) -> GrepResult:
    """过滤文本搜索结果中的依赖目录和敏感文件。"""

    if result.matches is not None:
        result.matches = [item for item in result.matches if _safe_result_path(item.get("path"))]
    return result


def _filter_glob_result(result: GlobResult) -> GlobResult:
    """过滤文件匹配结果中的依赖目录和敏感文件。"""

    if result.matches is not None:
        result.matches = _filter_file_infos(result.matches)
    return result


def _filter_raw_matches(value: list[Any] | str) -> list[Any] | str:
    """过滤原始搜索结果中的依赖目录和敏感文件。"""

    if not isinstance(value, list):
        return value
    return [
        item
        for item in value
        if not isinstance(item, dict) or _safe_result_path(item.get("path"))
    ]


def _denied(value: Any) -> str:
    """生成不暴露宿主绝对路径的拒绝原因。"""

    return f"code_analyze_path_denied: {normalize_virtual_path(value) or 'invalid_path'}"
