"""管理 Studio 用户目录内由后端实例独占的可丢弃资源。"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import BinaryIO

import portalocker

from app.services.backend_instance import current_backend_instance
from app.services.user_skills import user_skills_working_dir

_LOGGER = logging.getLogger(__name__)
_INSTANCE = re.compile(r"backend-[0-9a-f]{32}")
_GATE_TIMEOUT = 5
_area_lock = RLock()
_area: RuntimeTemporaryArea | None = None


def _runtime_root() -> Path:
    """定位当前 Studio 环境专用的临时资源根目录。"""
    return Path.home() / user_skills_working_dir() / "runtime" / "temporary"


def _safe_entry(info: os.stat_result) -> bool:
    """仅接受归当前用户所有的实体，拒绝链接和 Windows 重解析点。"""
    return (
        not stat.S_ISLNK(info.st_mode)
        and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        and (os.name == "nt" or info.st_uid == os.getuid())
    )


def _require_directory(path: Path) -> os.stat_result:
    """校验目录类型和所有权，拒绝把清理导向其他位置。"""
    info = path.lstat()
    if not _safe_entry(info) or not stat.S_ISDIR(info.st_mode):
        raise OSError(f"无效的 Studio 临时目录：{path.name}")
    return info


def _ensure_root(root: Path) -> None:
    """逐层创建真实目录，并要求临时区在 POSIX 上不向其他用户开放。"""
    for directory in (root.parent.parent, root.parent, root):
        directory.mkdir(mode=0o700, exist_ok=True)
        _require_directory(directory)


def _validate_root(root: Path) -> None:
    """复核整个用户目录边界，防止运行中被替换为链接。"""
    _require_directory(root.parent.parent)
    _require_directory(root.parent)
    info = _require_directory(root)
    if os.name != "nt" and info.st_mode & 0o077:
        raise PermissionError("Studio 临时资源目录必须是私有目录。")


def _lock_file(path: Path, *, flags: int = 0) -> BinaryIO:
    """打开单链接的普通锁文件，并核对打开前后的文件身份。"""
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not flags & os.O_CREAT:
            raise
    else:
        if not _safe_entry(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("无效的 Studio 临时区锁文件。")
    open_mode = os.O_RDWR | flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, open_mode, 0o600)
    try:
        opened = os.fstat(fd)
        named = path.lstat()
        if not (
            _safe_entry(named) and stat.S_ISREG(named.st_mode) and opened.st_nlink == 1
            and os.path.samestat(opened, named)
        ):
            raise OSError("Studio 临时区锁文件身份发生变化。")
        return os.fdopen(fd, "r+b")
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def _root_gate(root: Path) -> Iterator[None]:
    """串行化实例登记、领取孤儿和删除归属记录。"""
    _validate_root(root)
    with _lock_file(root / ".gate.lock", flags=os.O_CREAT) as handle:
        deadline = time.monotonic() + _GATE_TIMEOUT
        while True:
            try:
                portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
                break
            except portalocker.exceptions.AlreadyLocked:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        yield


def _ownership_record(instance_id: str) -> bytes:
    """生成与实例名严格绑定的归属记录。"""
    return f"devagentstudio-temporary:{instance_id}\n".encode("ascii")


def _check_tree(path: Path) -> None:
    """先验证整棵树，再清除只读属性，避免处理任何链接或未知实体。"""
    _require_directory(path)
    pending = [path]
    writable: list[tuple[Path, int]] = []
    while pending:
        entry = pending.pop()
        info = entry.lstat()
        directory = stat.S_ISDIR(info.st_mode)
        if not _safe_entry(info) or not (
            directory or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
        ):
            raise OSError("Studio 临时区含有不支持的文件类型。")
        if not info.st_mode & stat.S_IWUSR:
            writable.append((entry, stat.S_IMODE(info.st_mode) | stat.S_IWUSR))
        if directory:
            pending.extend(entry.iterdir())
    for entry, mode in writable:
        entry.chmod(mode)


def remove_temporary_tree(path: Path) -> None:
    """删除已获归属确认的资源目录，兼容只读 Skill 和规则快照。"""
    try:
        path.lstat()
    except FileNotFoundError:
        return
    _check_tree(path)
    shutil.rmtree(path)


def log_temporary_cleanup_failure(resource: str, instance_id: str, error: Exception) -> None:
    """仅记录清理边界和异常类型，避免输出资源内容。"""
    _LOGGER.warning(
        "temporary.cleanup_failed resource=%s instanceId=%s errorType=%s",
        resource, instance_id, type(error).__name__,
    )


@dataclass(slots=True)
class RuntimeTemporaryLease:
    """在资源仍可能被读取时保持实例锁有效。"""

    _area: RuntimeTemporaryArea
    _released: bool = False

    @property
    def directory(self) -> Path:
        """返回当前资源所属的后端目录。"""
        return self._area.validated_directory()

    @property
    def instance_id(self) -> str:
        """返回可安全用于日志的实例标识。"""
        return self._area.instance_id

    def release(self) -> None:
        """幂等释放资源引用，最后一个读取者可以完成退出清理。"""
        self._area.release(self)

    def check_resource(self, path: Path) -> None:
        """在清理前核对资源仍是原实例目录的直接子项。"""
        if path.parent != self.directory or path.name == "..":
            raise OSError("资源不属于当前 Studio 后端实例。")
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if not _safe_entry(info):
            raise OSError("Studio 后端临时资源已经改变归属。")


class RuntimeTemporaryArea:
    """持有跨进程实例锁，管理本进程仍在使用的临时资源。"""

    def __init__(self, root: Path, instance_id: str) -> None:
        """在根目录锁内建立本实例的归属，并回收上次退出的目录。"""
        if _INSTANCE.fullmatch(instance_id) is None:
            raise ValueError("无效的后端实例标识。")
        self.root = root
        self.instance_id = instance_id
        self.directory = root / instance_id
        self._owner_path = root / f"{instance_id}.lock"
        self._mutex = RLock()
        self._active = 0
        self._closing = False
        self._owner: BinaryIO | None = None
        _ensure_root(root)
        with _root_gate(root):
            self._reap_orphans()
            owner = _lock_file(self._owner_path, flags=os.O_CREAT | os.O_EXCL)
            try:
                portalocker.lock(owner, portalocker.LOCK_EX | portalocker.LOCK_NB)
                owner.write(_ownership_record(instance_id))
                owner.flush()
                self.directory.mkdir(mode=0o700)
                self._directory_info = _require_directory(self.directory)
            except BaseException:
                owner.close()
                raise
            self._owner = owner

    def validated_directory(self) -> Path:
        """核对实例目录仍是启动时创建的实体。"""
        _validate_root(self.root)
        if not os.path.samestat(self._directory_info, _require_directory(self.directory)):
            raise OSError("Studio 后端临时目录身份发生变化。")
        return self.directory

    def _reap_orphans(self) -> None:
        """只回收格式正确、归属一致且未被其他进程锁定的实例。"""
        for marker in self.root.glob("backend-*.lock"):
            instance_id = marker.stem
            if _INSTANCE.fullmatch(instance_id) is None:
                continue
            try:
                with _lock_file(marker) as owner:
                    try:
                        portalocker.lock(owner, portalocker.LOCK_EX | portalocker.LOCK_NB)
                    except portalocker.exceptions.AlreadyLocked:
                        continue
                    record = _ownership_record(instance_id)
                    if owner.read(len(record) + 1) != record:
                        continue
                    remove_temporary_tree(self.root / instance_id)
                marker.unlink()
            except (OSError, portalocker.exceptions.LockException) as error:
                log_temporary_cleanup_failure("orphan", instance_id, error)

    def acquire(self) -> RuntimeTemporaryLease:
        """登记一个临时资源，阻止退出时提前删除。"""
        with self._mutex:
            if self._closing:
                raise RuntimeError("Studio 临时资源区正在关闭。")
            self._active += 1
            return RuntimeTemporaryLease(self)

    def release(self, lease: RuntimeTemporaryLease) -> None:
        """减少有效引用数，必要时完成实例清理。"""
        with self._mutex:
            if not lease._released:
                lease._released = True
                self._active -= 1
                self._finish_if_idle()

    def close(self) -> None:
        """停止分配临时资源，仍有读取者时延迟清理。"""
        with self._mutex:
            self._closing = True
            self._finish_if_idle()

    def _finish_if_idle(self) -> None:
        """无人使用时回收实例；失败则留下记录供下次启动重试。"""
        owner = self._owner
        if not self._closing or self._active or owner is None:
            return
        self._owner = None
        try:
            with _root_gate(self.root):
                remove_temporary_tree(self.validated_directory())
                owner.close()
                self._owner_path.unlink()
        except (OSError, portalocker.exceptions.LockException) as error:
            log_temporary_cleanup_failure("instance", self.instance_id, error)
        finally:
            owner.close()


def start_runtime_temporary_area(instance_id: str) -> None:
    """在 FastAPI 启动时建立临时区并处理遗留实例。"""
    global _area
    with _area_lock:
        if _area is not None:
            _area.close()
        _area = RuntimeTemporaryArea(_runtime_root(), instance_id)


def close_runtime_temporary_area() -> None:
    """在 FastAPI 退出时关闭实例临时区。"""
    with _area_lock:
        if _area is not None:
            _area.close()


def acquire_runtime_temporary_lease() -> RuntimeTemporaryLease:
    """获取当前后端资源引用，独立服务调用时按需初始化。"""
    global _area
    with _area_lock:
        if _area is None:
            _area = RuntimeTemporaryArea(_runtime_root(), current_backend_instance().instance_id)
        return _area.acquire()
