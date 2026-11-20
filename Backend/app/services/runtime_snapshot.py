"""为 Agent 提供可共享的只读快照，并随最后一个持有者释放磁盘副本。"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Hashable
from pathlib import Path
from threading import Lock
from typing import Any
from weakref import WeakValueDictionary, finalize

from deepagents.backends import FilesystemBackend, StateBackend
from deepagents.backends.protocol import DeleteResult, EditResult, FileUploadResponse, WriteResult

from app.services.runtime_temporary import (
    RuntimeTemporaryLease,
    acquire_runtime_temporary_lease,
    log_temporary_cleanup_failure,
    remove_temporary_tree,
)


class _ReadOnlySnapshot:
    """在工具入口禁止所有写操作，避免依赖各平台的只读属性。"""

    def write(self, file_path: str, content: str) -> WriteResult:
        """拒绝写文件。"""
        return WriteResult(error=f"只读快照不允许写入：{file_path}")

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """拒绝编辑文件。"""
        return EditResult(error=f"只读快照不允许编辑：{file_path}")

    def delete(self, file_path: str) -> DeleteResult:
        """拒绝删除文件。"""
        return DeleteResult(error=f"只读快照不允许删除：{file_path}")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """拒绝批量上传文件。"""
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]


class _EmptySnapshotBackend(_ReadOnlySnapshot, StateBackend):
    """在原虚拟路径提供空快照，不分配临时目录或读取工作流文件状态。"""

    def _read_files(self) -> dict[str, Any]:
        """始终返回空文件集合。"""
        return {}


EMPTY_SNAPSHOT_BACKEND = _EmptySnapshotBackend()


def _release_snapshot(path: Path, lease: RuntimeTemporaryLease, kind: str) -> None:
    """删除磁盘副本，失败也释放读取引用，以便后续启动回收。"""
    try:
        lease.check_resource(path)
        remove_temporary_tree(path)
    except OSError as error:
        log_temporary_cleanup_failure(kind, lease.instance_id, error)
    finally:
        lease.release()


class _SnapshotBackend(_ReadOnlySnapshot, FilesystemBackend):
    """由虚拟文件后端持有快照，Agent 和缓存可以各自保留这个引用。"""

    def __init__(self, path: Path, lease: RuntimeTemporaryLease, kind: str) -> None:
        """挂载已经验证的目录，最后一个引用消失时释放。"""
        super().__init__(root_dir=path, virtual_mode=True)
        cleanup = finalize(self, _release_snapshot, path, lease, kind)
        # 解释器退出时仍被持有的快照留给下一进程回收。
        cleanup.atexit = False


class RuntimeSnapshotCache:
    """按快照身份共享后端，弱引用本身不会延长文件生命周期。"""

    def __init__(self, kind: str) -> None:
        """为一种快照保存独立的创建锁和缓存。"""
        self._kind = kind
        self._lock = Lock()
        self._backends: WeakValueDictionary[Hashable, _SnapshotBackend] = WeakValueDictionary()

    def get(self, key: Hashable, populate: Callable[[Path], None]) -> FilesystemBackend:
        """串行发布已完整生成的快照，构建失败不留下部分结果。"""
        with self._lock:
            cached = self._backends.get(key)
            if cached is not None:
                return cached
            lease = acquire_runtime_temporary_lease()
            path: Path | None = None
            try:
                path = Path(tempfile.mkdtemp(prefix=f"{self._kind}-", dir=lease.directory))
                populate(path)
                backend = _SnapshotBackend(path, lease, self._kind)
            except BaseException:
                if path is None:
                    lease.release()
                else:
                    _release_snapshot(path, lease, self._kind)
                raise
            self._backends[key] = backend
            return backend
