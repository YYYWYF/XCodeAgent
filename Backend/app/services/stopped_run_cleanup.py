"""暂停控制超时后继续等待真实工作退出，并自动释放本次执行占用。"""

import asyncio
import logging
from typing import Any

from app.domain.execution_recovery import DurableExecutionStatus
from app.persistence.execution_recovery import get_execution, reconcile_interrupted_execution_status
from app.services.application_lifecycle import load_application_lifecycle
from app.services.failed_run_cleanup import drain_failed_run, release_stopped_execution_resources


_pending: dict[tuple[str, str], asyncio.Task[Any]] = {}


async def finish_stopped_run(workspace: str, run_id: str, task: asyncio.Task[Any] | None) -> None:
    """真实线程、命令及外层任务退出后再释放锁，保留原始恢复现场。"""

    await drain_failed_run(run_id)
    if task is not None and not task.done():
        await asyncio.wait([task])
    lifecycle = load_application_lifecycle(workspace)
    execution = lifecycle.active_executions.get(run_id) if lifecycle else None
    if execution is None:
        return
    from app.protocols.workflow.lifecycle import stop_workflow_lifecycle

    source = await get_execution(workspace, run_id)
    if source is not None:
        await reconcile_interrupted_execution_status(
            workspace=workspace, run_id=run_id, status=DurableExecutionStatus.CANCELLED,
        )
    stop_workflow_lifecycle(workspace, run_id=run_id, phase=execution.phase)
    release_stopped_execution_resources(workspace, run_id)


def continue_stopped_run_cleanup(workspace: str, run_id: str, task: asyncio.Task[Any] | None) -> None:
    """每个 Run 仅保留一个后台暂停收口任务，控制请求退出不会丢失清理。"""

    key = (workspace, run_id)
    if key in _pending:
        return
    cleanup = asyncio.create_task(finish_stopped_run(workspace, run_id, task))
    _pending[key] = cleanup

    def completed(done: asyncio.Task[Any]) -> None:
        """回收后台清理引用并保留真实清理异常，避免静默吞掉占用问题。"""
        _pending.pop(key, None)
        if not done.cancelled() and (error := done.exception()) is not None:
            logging.getLogger(__name__).error("暂停运行清理未完成 runId=%s", run_id, exc_info=error)

    cleanup.add_done_callback(completed)
