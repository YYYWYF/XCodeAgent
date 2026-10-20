"""失败运行退出后释放占用，保留原执行与 checkpoint 的恢复资格。"""

from __future__ import annotations

import asyncio
import logging
from inspect import iscoroutine
from collections.abc import Awaitable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.domain.application_lifecycle import (
    ExecutionResourceClaim, ExecutionResourceReason, ExecutionResourceRole,
    WorkbenchExecution, WorkbenchExecutionStatus,
)
from app.services import application_lifecycle as lifecycle_service
from app.services.run_workers import active_worker_runs
from app.services.workspace_process_registry import workspace_process_registry


_cleanup_tasks: set[asyncio.Task[Any]] = set()
_run_drains: dict[str, asyncio.Task[Any]] = {}
FOREGROUND_CLEANUP_SECONDS = 5.0
_process_cleanup_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="run-cleanup")


def _cleanup_done(task: asyncio.Task[Any]) -> None:
    """保留清理失败的诊断，并及时回收后台任务引用。"""
    _cleanup_tasks.discard(task)
    if not task.cancelled() and (error := task.exception()) is not None:
        logging.getLogger(__name__).error("失败运行清理未完成", exc_info=error)


async def shield_run_cleanup(cleanup: Coroutine[Any, Any, Any]) -> Any:
    """前台最多等待五秒；连接取消或超时后仍持有真正的后台清理任务。"""
    task = asyncio.create_task(cleanup)
    _cleanup_tasks.add(task)
    task.add_done_callback(_cleanup_done)
    done, _ = await asyncio.wait({task}, timeout=FOREGROUND_CLEANUP_SECONDS)
    return task.result() if done else None


def begin_failed_run_cleanup(run_id: str, close: Awaitable[Any] | None = None) -> asyncio.Task[Any]:
    """先通知命令退出，再关闭 Graph；同一 Run 的前后台等待共用一次真实清理。"""
    workspace_process_registry.cancel_run(run_id)
    existing = _run_drains.get(run_id)
    if existing is not None:
        if iscoroutine(close):
            close.close()
        return existing
    task = asyncio.create_task(_drain_failed_run(run_id, close))
    _run_drains[run_id] = task
    _cleanup_tasks.add(task)

    def completed(done: asyncio.Task[Any]) -> None:
        """退出后移除单次清理登记，不积累历史 Run 或吞掉清理异常。"""
        if _run_drains.get(run_id) is done:
            _run_drains.pop(run_id, None)
        _cleanup_done(done)

    task.add_done_callback(completed)
    return task


async def _drain_failed_run(run_id: str, close: Awaitable[Any] | None) -> None:
    """后台保留实际退出边界；线程登记采用非阻塞观察，不争抢业务线程池。"""
    closing = asyncio.ensure_future(close) if close is not None else None
    try:
        while True:
            # Graph 关闭和命令强制回收同时推进，避免关闭操作本身等着命令退出。
            processes = workspace_process_registry.active_run_processes(run_id)
            if processes:
                processes = await asyncio.get_running_loop().run_in_executor(
                    _process_cleanup_executor,
                    workspace_process_registry.wait_run_processes, run_id, 0.2,
                )
            drained = run_id not in active_worker_runs()
            from app.services.retry_preparation import pending_checkpoint_preparations
            drained = drained and not pending_checkpoint_preparations(run_id)
            if drained and not processes:
                if closing is not None:
                    try:
                        await closing
                    except Exception as exc:
                        # 关闭流的次生错误不能覆盖原失败，也不能阻止已经退出的资源释放。
                        logging.getLogger(__name__).warning(
                            "Graph 关闭异常 runId=%s type=%s", run_id, type(exc).__name__,
                        )
                return
            await asyncio.sleep(0.05)
    finally:
        if closing is not None and not closing.done():
            closing.cancel()


async def wait_failed_run(run_id: str, close: Awaitable[Any] | None = None) -> bool:
    """限制错误回执的等待时间，超时仍由后台等待真实退出并保留资源保护。"""
    task = begin_failed_run_cleanup(run_id, close)
    done, _ = await asyncio.wait({task}, timeout=FOREGROUND_CLEANUP_SECONDS)
    if done:
        task.result()
    return bool(done)


async def drain_failed_run(run_id: str) -> None:
    """仅供后台收尾等待真实退出，前台错误回执使用有界等待。"""
    await asyncio.shield(begin_failed_run_cleanup(run_id))


async def require_failed_run_drained(run_id: str, close: Awaitable[Any] | None = None) -> None:
    """重试准备也有等待上限，旧工作未退出时明确收口，禁止并发写入。"""
    if not await wait_failed_run(run_id, close):
        from app.domain.execution_recovery import RecoveryExecutionError
        raise RecoveryExecutionError(
            "RUN_CLEANUP_PENDING",
            "后台工作尚未退出，已保留原失败节点和提问。请稍后重试；若持续无法退出，请重启 Backend 后重试。",
        )


async def observe_drained_failure(*, workspace: str, run_id: str, thread_id: str,
                                  workflow_scope: str | None, graph: Any, error: BaseException) -> None:
    """断线打断错误回执时，仍在退出后提交失败事实，避免恢复等待旧租约过期。"""
    from app.persistence.execution_recovery import get_execution
    from app.domain.execution_recovery import DurableExecutionStatus, RecoveryExecutionError
    from app.services.execution_recovery import observe_execution_failed
    from app.services.workflow_reentry import resolve_current_node_entry_boundary

    source = await get_execution(workspace, run_id)
    if source is None or source.status == DurableExecutionStatus.FAILED:
        return
    target = None
    try:
        boundary = await resolve_current_node_entry_boundary(workspace=workspace, source=source, graph=graph)
        target = boundary.target_node
    except RecoveryExecutionError:
        # 没有精确 checkpoint 时只登记失败，不能猜节点或签发不安全的重试。
        pass
    await observe_execution_failed(workspace=workspace, run_id=run_id, thread_id=thread_id,
                                   workflow_scope=workflow_scope, exception=error, authoritative_node=target)


def release_failed_execution_resources(workspace: str | None, run_id: str):
    """仅移除已退出 FAILED 执行的资源锁，保留失败身份、诊断和资源键供重试。"""
    return _release_execution_resources(workspace, run_id, WorkbenchExecutionStatus.FAILED)


def release_stopped_execution_resources(workspace: str | None, run_id: str):
    """实际工作退出后释放暂停执行的锁，保留节点、提问和资源键供继续执行。"""
    return _release_execution_resources(workspace, run_id, WorkbenchExecutionStatus.STOPPED)


def _release_execution_resources(workspace: str | None, run_id: str, status: WorkbenchExecutionStatus):
    """按精确运行身份释放已退出占用，不移除任何其他运行的锁。"""
    if not workspace:
        return None
    path = lifecycle_service.application_lifecycle_path(workspace)
    with lifecycle_service._application_lifecycle_lock(path):
        current = lifecycle_service.load_application_lifecycle(workspace)
        execution = current.active_executions.get(run_id) if current else None
        if execution is None or execution.status != status:
            return current
        if (not workspace_process_registry.is_run_cancelled(run_id)
                or run_id in active_worker_runs() or workspace_process_registry.active_run_processes(run_id)):
            raise lifecycle_service.ApplicationLifecycleConflictError("失败任务仍在退出，不能释放执行占用。")
        locks = lifecycle_service._resource_locks_without_run(current.resource_locks, run_id)
        if locks == current.resource_locks:
            return current
        return lifecycle_service.write_application_lifecycle(
            workspace,
            current.model_copy(update={
                "resource_locks": locks,
                "updated_at": lifecycle_service.utc_now(),
                "revision": current.revision + 1,
            }),
            expected_revision=current.revision,
        )


def failed_execution_retry_claims(execution: WorkbenchExecution) -> list[ExecutionResourceClaim]:
    """从失败记录的完整资源键重新申请恢复资源，不依赖已释放的锁索引。"""
    primary = "application:application" if execution.scope == "application" else f"{execution.scope}:{execution.target_id}"
    claims = []
    for key in execution.resource_keys:
        resource_type, target_id = key.split(":", 1)
        claims.append(ExecutionResourceClaim(
            type=resource_type, targetId=target_id,
            role=ExecutionResourceRole.PRIMARY if key == primary else ExecutionResourceRole.DEPENDENCY,
            reason=ExecutionResourceReason.PRIMARY_TARGET if key == primary else ExecutionResourceReason.PLAN_DEPENDENCY,
        ))
    return claims
