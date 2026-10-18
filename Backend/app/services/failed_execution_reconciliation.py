"""把已落盘的执行失败同步到工作台投影，不让旧确认阻断原节点重试。"""

from app.domain.application_lifecycle import ApplicationLifecycleError, WorkbenchExecutionStatus
from app.domain.execution_recovery import DurableExecutionStatus
from app.services import application_lifecycle as lifecycle_service


def reconcile_failed_execution_lifecycle(source):
    """只收口同一失败执行的旧确认与占用，不改正常确认、其他运行或已经结束的任务。"""
    if source.execution_kind != "workbench" or source.status not in {
        DurableExecutionStatus.FAILED, DurableExecutionStatus.INTERRUPTED,
    }:
        return None
    from app.protocols.workflow.run_control import workflow_run_registry
    from app.services.run_workers import active_worker_runs
    from app.services.workspace_process_registry import workspace_process_registry
    from app.workspace.run_lease import workspace_run_leases

    path = lifecycle_service.application_lifecycle_path(source.workspace)
    with lifecycle_service._application_lifecycle_lock(path):
        current = lifecycle_service.load_application_lifecycle(source.workspace)
        execution = current.active_executions.get(source.run_id) if current else None
        if execution is None or execution.thread_id != source.thread_id:
            return current
        # Durable 终态是失败事实；旧 pending 只属于已失败执行，不能再被确认或阻止交接。
        failed = execution.model_copy(update={
            "status": WorkbenchExecutionStatus.FAILED, "pending_interaction": None,
            # 交接提交后首节点尚未运行时，索引没有 current_node；不能把有效入口覆盖为 null。
            "phase": source.current_node or source.first_node or execution.phase,
            "error": execution.error or ApplicationLifecycleError(
                code=source.failure.code if source.failure else "execution_interrupted",
                message=(source.failure.diagnostic_message if source.failure else None) or "执行已中断，请重试。",
                recoverable=True, occurredAt=source.updated_at,
            ),
        })
        locks = current.resource_locks
        # 沿用实际工作退出边界；失败回执并不等于后台线程已经停止，绝不提前解锁。
        exited = (not workflow_run_registry.is_active(source.run_id, workspace=source.workspace)
                  and source.run_id not in active_worker_runs()
                  and not workspace_process_registry.active_run_processes(source.run_id))
        if exited:
            workspace_process_registry.cancel_run(source.run_id)
            workspace_run_leases.release_run(workspace_root=source.workspace, run_id=source.run_id)
            locks = lifecycle_service._resource_locks_without_run(locks, source.run_id)
        if failed == execution and locks == current.resource_locks:
            return current
        now = lifecycle_service.utc_now()
        executions = {**current.active_executions, source.run_id: failed.model_copy(update={"updated_at": now})}
        return lifecycle_service.write_application_lifecycle(source.workspace, current.model_copy(update={
            "active_executions": executions, "resource_locks": locks,
            "revision": current.revision + 1, "updated_at": now,
        }), expected_revision=current.revision)
