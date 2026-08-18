"""单元测试生成 Agent 的文件写入边界。"""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import PurePosixPath
from typing import Any

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    WriteResult,
)


_CURRENT_TEST_PATHS: ContextVar[tuple[str, ...]] = ContextVar(
    "xcodeagent_test_generation_paths",
    default=(),
)


def test_generation_path_scope(paths: list[str] | tuple[str, ...]):
    """在一次测试生成调用中安装精确的测试文件范围。"""

    from contextlib import contextmanager

    @contextmanager
    def scope():
        """安装并在调用完成后恢复测试路径范围。"""

        token = _CURRENT_TEST_PATHS.set(
            tuple(path for path in (_normalize_scope(item) for item in paths) if path)
        )
        try:
            yield
        finally:
            _CURRENT_TEST_PATHS.reset(token)

    return scope()


def is_test_generation_path_allowed(path: str) -> bool:
    """判断虚拟工作区路径是否属于前后端单元测试目录。"""

    candidate = _normalize_candidate(path)
    if not candidate:
        return False
    lowered = candidate.casefold()
    frontend_prefix = "frontend/tests/"
    backend_prefix = "backend/src/test/java/"
    if not (lowered.startswith(frontend_prefix) or lowered.startswith(backend_prefix)):
        return False
    if lowered.startswith(frontend_prefix):
        # 前端测试要求平铺在 tests 根目录，避免 Agent 通过嵌套路径绕过命名约定。
        return "/" not in candidate[len(frontend_prefix) :] and lowered.endswith(
            (".test.ts", ".test.tsx")
        )
    return lowered.endswith(".java")


class ScopedTestGenerationBackend(BackendProtocol):
    """给文件系统 Backend 增加只能写测试目录的硬边界。"""

    def __init__(self, delegate: BackendProtocol):
        self._delegate = delegate

    def write(self, file_path: str, content: str) -> WriteResult:
        """拒绝测试目录之外的新增文件。"""

        if not is_test_generation_path_allowed(file_path):
            return WriteResult(error=f"test_generation_path_denied: {file_path}")
        return self._delegate.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """拒绝测试目录之外的修改。"""

        if not is_test_generation_path_allowed(file_path):
            return EditResult(error=f"test_generation_path_denied: {file_path}")
        return self._delegate.edit(file_path, old_string, new_string, replace_all)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """批量写入前统一校验每个测试路径。"""

        if any(not is_test_generation_path_allowed(path) for path, _ in files):
            return [
                FileUploadResponse(
                    path=path,
                    error=(
                        None
                        if is_test_generation_path_allowed(path)
                        else f"test_generation_path_denied: {path}"
                    ),
                )
                for path, _ in files
            ]
        return self._delegate.upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """仅允许批量读取测试目录文件，其他读取交由默认 Backend。"""

        return self._delegate.download_files(paths)

    def __getattr__(self, name: str) -> Any:
        """转发未涉及写入策略的文件系统能力。"""

        return getattr(self._delegate, name)


def _normalize_scope(value: Any) -> str:
    """规范化测试路径范围并拒绝目录穿越。"""

    normalized = str(value or "").strip().replace("\\", "/").lstrip("/")
    return normalized if normalized and ".." not in PurePosixPath(normalized).parts else ""


def _normalize_candidate(value: Any) -> str:
    """规范化工具传入的虚拟绝对路径。"""

    normalized = str(value or "").strip().replace("\\", "/").lstrip("/")
    if not normalized or ".." in PurePosixPath(normalized).parts:
        return ""
    return normalized
