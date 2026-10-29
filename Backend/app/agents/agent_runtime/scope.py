"""单次 Runtime 任务的精确文件写入边界，不共享可变上下文。"""

from pathlib import Path, PurePosixPath
from typing import Any

from deepagents.backends.protocol import BackendProtocol, EditResult, FileUploadResponse, WriteResult
from app.services.runtime_agent_module_policy import module_path_allowed
from app.services.runtime_business_paths import runtime_business_path_error


def runtime_source_path(path: str, *, bootstrap: bool = False) -> bool:
    """仅接收当前工程中的精确 Python 文件，bootstrap 可额外修改依赖声明。"""

    if not isinstance(path, str) or "\\" in path or "\x00" in path:
        return False
    parts = PurePosixPath(path).parts
    if not parts or str(PurePosixPath(path)) != path or any(
        part in {"..", "."} or part.startswith(".") or any(char in part for char in "*?[]")
        for part in parts
    ):
        return False
    if bootstrap and path == "agent-runtime/pyproject.toml":
        return True
    return (path.endswith(".py") and
            (path.startswith("agent-runtime/src/app/") or path.startswith("agent-runtime/tests/")))


class RuntimeTaskBackend(BackendProtocol):
    """转发读取，在实际写入前核对精确路径和物理路径边界。"""

    def __init__(self, delegate: BackendProtocol, workspace: str, paths: tuple[str, ...], module_policy: dict | None = None) -> None:
        """冻结本任务文件清单，禁止并发 Agent 互相扩充权限。"""

        self._delegate = delegate
        self._root = Path(workspace).resolve()
        self._paths = frozenset(paths)
        self._module_policy = module_policy

    def _allowed(self, path: str) -> bool:
        """逐层拒绝符号链接，授权仅精确命中文件，不授权其子路径。"""

        relative = path[1:] if path.startswith("/") else path
        permitted = (module_path_allowed(relative, self._module_policy) if self._module_policy is not None
                     else runtime_business_path_error(relative, bootstrap=True) is None)
        if relative not in self._paths or not permitted:
            return False
        target = self._root
        for part in PurePosixPath(relative).parts:
            target = target / part
            if target.is_symlink():
                return False
        return target.resolve().is_relative_to(self._root)

    def ls(self, path: str):
        """转发由权限中间件限制的 Runtime 目录读取。"""
        return self._delegate.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2_000):
        """读取真实模板与任务文件，读取范围仍受权限中间件约束。"""
        return self._delegate.read(file_path, offset, limit)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None):
        """默认只搜索 Runtime 工程，不从工作区根搜索。"""
        return self._delegate.grep(pattern, path or "/agent-runtime/src/app", glob)

    def glob(self, pattern: str, path: str = "/"):
        """默认只枚举 Runtime 源码。"""
        return self._delegate.glob(pattern, "/agent-runtime/src/app" if path == "/" else path)

    def write(self, file_path: str, content: str) -> WriteResult:
        """只创建当前任务明确授权的文件。"""
        if not self._allowed(file_path):
            return WriteResult(error="Runtime 任务无权写入该文件。")
        return self._delegate.write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """只修改当前任务明确授权的文件。"""
        if not self._allowed(file_path):
            return EditResult(error="Runtime 任务无权修改该文件。")
        return self._delegate.edit(file_path, old_string, new_string, replace_all)

    def download_files(self, paths: list[str]):
        """转发权限中间件已批准的批量读取。"""
        return self._delegate.download_files(paths)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """批量操作任一文件越界即整体拒绝，不产生部分写入。"""
        if any(not self._allowed(path) for path, _ in files):
            return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]
        return self._delegate.upload_files(files)

    def __getattr__(self, name: str) -> Any:
        """透传文件协议的非写入元数据。"""
        return getattr(self._delegate, name)
