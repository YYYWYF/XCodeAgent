"""重试准备的等待期限和未启动交接回退，不改变业务节点或准入条件。"""

import asyncio
from collections.abc import Awaitable
from typing import Any

from app.services import application_lifecycle as lifecycle_service

CHECKPOINT_PREPARATION_SECONDS = 5.0
_pending: dict[str, set[asyncio.Future[Any]]] = {}


class CheckpointPreparationTimeout(TimeoutError):
    """区分平台恢复准备超时与需要用户修复的模型连接超时。"""


def pending_checkpoint_preparations(run_id: str) -> bool:
    """让真实退出判断包含取消后尚未返回的 checkpoint 准备任务。"""
    return any(not task.done() for task in _pending.get(run_id, ()))


async def await_checkpoint_preparation(operation: Awaitable[Any], run_id: str) -> Any:
    """限制元数据准备等待，取消未返回的操作并保留其真实退出登记。"""
    task = asyncio.ensure_future(operation)
    _pending.setdefault(run_id, set()).add(task)

    def completed(done: asyncio.Future[Any]) -> None:
        """收回准备任务引用，并消费超时之后才返回的次生异常。"""
        tasks = _pending.get(run_id)
        if tasks is not None:
            tasks.discard(done)
            if not tasks:
                _pending.pop(run_id, None)
        if not done.cancelled():
            done.exception()

    task.add_done_callback(completed)
    try:
        done, _ = await asyncio.wait({task}, timeout=CHECKPOINT_PREPARATION_SECONDS)
        if done:
            return task.result()
        raise CheckpointPreparationTimeout("checkpoint 恢复准备超时。")
    finally:
        if not task.done():
            task.cancel()


def restore_retry_source(workspace: str, source, before, new_run_id: str) -> None:
    """只撤销尚未启动的本次交接，保留其他执行、产物及并发产生的新状态。"""
    path = lifecycle_service.application_lifecycle_path(workspace)
    with lifecycle_service._application_lifecycle_lock(path):
        current = lifecycle_service.load_application_lifecycle(workspace)
        if current is None or before is None:
            return
        updates = {}
        if source.execution_kind == "workbench":
            child = current.active_executions.get(new_run_id)
            original = before.active_executions.get(source.run_id)
            if child is None and current.active_run_id == new_run_id and original is not None:
                # 上游重新生成跨入规划 Graph 的未启动交接，也恢复原工作台入口。
                updates.update(active_run_id=before.active_run_id,
                    active_executions={**current.active_executions, source.run_id: original},
                    initialization=before.initialization, active_formal_revision=before.active_formal_revision,
                    resource_locks=lifecycle_service._resource_locks_without_run(current.resource_locks, new_run_id))
            elif child is None or original is None or child.thread_id != source.thread_id:
                return
            else:
                executions = dict(current.active_executions)
                executions.pop(new_run_id)
                executions.setdefault(source.run_id, original)
                updates.update(active_executions=executions,
                    resource_locks=lifecycle_service._resource_locks_without_run(current.resource_locks, new_run_id))
        elif current.active_run_id == new_run_id:
            updates["active_run_id"] = before.active_run_id
        else:
            return
        lifecycle_service.write_application_lifecycle(workspace, current.model_copy(update={
            **updates, "revision": current.revision + 1, "updated_at": lifecycle_service.utc_now(),
        }), expected_revision=current.revision)


async def abandon_prepared_retry(*, workspace: str, source, before, new_run_id: str, lease=None) -> None:
    """未启动的重试回退到原入口，并结束新执行登记；租约释放不依赖回退写盘成功。"""
    from app.domain.execution_recovery import DurableExecutionStatus
    from app.persistence.execution_recovery import get_execution
    from app.services.execution_recovery import observe_execution_finished
    from app.services.execution_cleanup import retry_cleanup_persistence

    async def persist() -> None:
        """幂等回退当前交接，重试落盘不替换原失败节点或上次提问。"""
        restore_retry_source(workspace, source, before, new_run_id)
        child = await get_execution(workspace, new_run_id)
        if child is not None and child.status == DurableExecutionStatus.RUNNING:
            await observe_execution_finished(workspace=workspace, run_id=new_run_id,
                thread_id=source.thread_id, workflow_scope=source.workflow_scope,
                # 未开始业务的 child 属于已撤销准备，不能成为规划恢复列表中的新失败入口。
                status=DurableExecutionStatus.CANCELLED)

    try:
        await retry_cleanup_persistence(persist)
    finally:
        from app.workspace.run_lease import workspace_run_leases
        try:
            if lease is not None:
                lease.release()
        finally:
            workspace_run_leases.release_run(workspace_root=workspace, run_id=new_run_id)
