from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, TypeVar


T = TypeVar("T")
_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="screenshot-ui-audit",
)
_LOCKS_GUARD = threading.Lock()
_MANIFEST_LOCKS: dict[str, threading.Lock] = {}


def submit_background_audit(job: Callable[[], None]) -> Future[None]:
    """把不阻塞 UI 确认的视觉审查提交到进程级后台线程池。"""

    return _EXECUTOR.submit(job)


def update_manifest_atomically(key: str, updater: Callable[[], T]) -> T:
    """按工作区串行执行短暂的 Manifest 读改写，避免多页审查相互覆盖。"""

    with _LOCKS_GUARD:
        lock = _MANIFEST_LOCKS.setdefault(key, threading.Lock())
    with lock:
        return updater()

