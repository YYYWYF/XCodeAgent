"""执行边界的非阻断维护和失败落盘补偿，不承担业务节点行为。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.services.failed_run_cleanup import _cleanup_tasks, _cleanup_done

logger = logging.getLogger(__name__)
HOUSEKEEPING_SECONDS = 2.0
CLEANUP_WRITE_SECONDS = 2.0
_housekeeping: dict[str, asyncio.Task] = {}


async def retry_cleanup_persistence(operation: Callable[[], Awaitable[None]]) -> None:
    """状态写入失败后后台补写，让已退出执行的进程内释放独立完成。"""
    initial = asyncio.create_task(operation())
    _cleanup_tasks.add(initial)
    initial.add_done_callback(_cleanup_done)
    done, _ = await asyncio.wait({initial}, timeout=CLEANUP_WRITE_SECONDS)
    if done and not initial.cancelled() and initial.exception() is None:
        return
    logger.warning("执行收尾落盘未完成，后台继续补写")

    async def retry() -> None:
        """瞬时存储故障恢复后自动补齐终态，失败不影响其他执行退出。"""
        if not initial.done():
            try:
                await initial
                return
            except Exception:
                pass
        while True:
            await asyncio.sleep(.25)
            try:
                await operation()
                return
            except Exception:
                # 保持补写责任；只在首次失败记录诊断，避免永久存储故障刷屏。
                continue

    task = asyncio.create_task(retry())
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_done)


async def best_effort_checkpoint_housekeeping(workspace: str | None, project_id: str | None = None) -> None:
    """例行历史清理失败或超时不拒绝本轮业务；同工作区不重复堆积维护任务。"""
    from app.persistence.checkpoints import cleanup_workflow_checkpoints
    key = str(workspace or project_id or "")
    if key in _housekeeping:
        return

    async def clean() -> None:
        """取消维护时尽快退出，异常只记录类型，不暴露工作区内容。"""
        try:
            await cleanup_workflow_checkpoints(workspace=workspace, project_id=project_id)
        except Exception as exc:
            logger.warning("跳过本次 checkpoint 例行清理 type=%s", type(exc).__name__)

    task = asyncio.create_task(clean())
    _housekeeping[key] = task

    def completed(done: asyncio.Task) -> None:
        """回收维护任务并消费其退出异常。"""
        if _housekeeping.get(key) is done:
            _housekeeping.pop(key, None)
        if not done.cancelled():
            done.exception()

    task.add_done_callback(completed)
    try:
        await asyncio.wait({task}, timeout=HOUSEKEEPING_SECONDS)
    finally:
        if not task.done():
            task.cancel()


async def finalize_runtime_ownership(*, heartbeat, unregister: Callable[[], None], lease=None, planning_lock=None) -> None:
    """每层内存释放独立保证执行，前一层异常不会吞掉后一层的释放责任。"""
    from app.services.execution_lease_heartbeat import stop_execution_heartbeat
    try:
        await stop_execution_heartbeat(heartbeat)
    finally:
        try:
            unregister()
        finally:
            try:
                if lease is not None:
                    lease.release()
            finally:
                if planning_lock is not None:
                    planning_lock.release()
