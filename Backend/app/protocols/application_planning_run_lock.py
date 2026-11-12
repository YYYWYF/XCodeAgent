"""协调 application planning 同线程写运行与权威只读恢复。"""

from __future__ import annotations

import asyncio


_APPLICATION_PLANNING_RUN_LOCKS: dict[str, asyncio.Lock] = {}


def application_planning_run_lock(thread_id: str) -> asyncio.Lock:
    """返回指定 application planning thread 的共享运行锁。"""

    lock = _APPLICATION_PLANNING_RUN_LOCKS.get(thread_id)
    if lock is None:
        # 单进程事件循环内创建锁不需要额外互斥；不同 thread 保持并行。
        lock = asyncio.Lock()
        _APPLICATION_PLANNING_RUN_LOCKS[thread_id] = lock
    return lock


def clear_application_planning_run_locks(thread_ids: set[str]) -> int:
    """在应用删除完成后移除未占用的 application planning thread 锁。"""

    removed = 0
    for thread_id in thread_ids:
        lock = _APPLICATION_PLANNING_RUN_LOCKS.get(thread_id)
        if lock is not None and not lock.locked():
            _APPLICATION_PLANNING_RUN_LOCKS.pop(thread_id, None)
            removed += 1
    return removed
