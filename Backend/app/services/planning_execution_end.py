"""结束正式修订的规划执行，保留产物并释放本次规划占用。"""

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.execution_recovery import DurableExecutionStatus
from app.persistence.execution_recovery import get_execution, update_execution_status
from app.services import application_lifecycle as lifecycle_service


async def stop_planning_execution(workspace: str, thread_id: str):
    """等待规划工作真实退出并保留本轮修订，以原 checkpoint 签发继续生成动作。"""
    from app.protocols.workflow.run_control import workflow_run_registry
    from app.workspace.run_lease import workspace_run_leases

    current = lifecycle_service.load_application_lifecycle(workspace)
    if current is None or current.initialization.thread_id != thread_id:
        raise ValueError("当前规划线程与应用不匹配。")
    active = current.active_formal_revision
    run_id = active.planning_run_id if active and active.planning_thread_id == thread_id else current.active_run_id
    source = await get_execution(workspace, run_id) if run_id else None
    if source is None or source.execution_kind != "application_planning" or source.thread_id != thread_id:
        raise ValueError("当前没有可停止的规划执行，请同步当前任务。")
    outcome = await workflow_run_registry.cancel_and_wait(run_id, workspace=workspace)
    if outcome not in {"cancelled", "not_running"}:
        raise RuntimeError("后台仍在退出，请再次点击停止生成；本轮修改和继续入口已保留。")
    # Runtime 的取消收尾已经完成后再标记中断；停止不能归档或删除正式修订。
    await update_execution_status(workspace=workspace, run_id=run_id, status=DurableExecutionStatus.INTERRUPTED)
    workspace_run_leases.release_run(workspace_root=workspace, run_id=run_id)
    return lifecycle_service.load_application_lifecycle(workspace)


async def end_planning_execution(workspace: str, thread_id: str):
    """先等待真实工作退出，再收口同一 Run/Thread 的修订，不改变 Agent 或阶段准入。"""
    from app.protocols.workflow.run_control import workflow_run_registry
    from app.workspace.run_lease import workspace_run_leases

    current = lifecycle_service.load_application_lifecycle(workspace)
    active = current.active_formal_revision if current else None
    if active is None or active.planning_thread_id != thread_id:
        return current
    run_id = active.planning_run_id or current.active_run_id
    source = await get_execution(workspace, run_id) if run_id else None
    # 已交接开发的执行由工作台 End 管理；规划结束不能取消其工作或删除其修订。
    if source is not None and (source.execution_kind != "application_planning" or source.thread_id != thread_id):
        return current
    if run_id:
        outcome = await workflow_run_registry.cancel_and_wait(run_id, workspace=workspace, ending=True)
        if outcome not in {"cancelled", "not_running"}:
            raise RuntimeError("后台仍在退出，尚未结束本轮设计，请再次点击结束任务。")
        if source is not None:
            from app.services.execution_recovery import observe_execution_finished
            await observe_execution_finished(workspace=workspace, run_id=run_id,
                thread_id=thread_id, workflow_scope=source.workflow_scope, status=DurableExecutionStatus.CANCELLED)
            # 用户 End 同样收口已处于等待确认/中断的记录，不能再被恢复投影重新签发。
            await update_execution_status(workspace=workspace, run_id=run_id,
                status=DurableExecutionStatus.CANCELLED, ended=True)
        workspace_run_leases.release_run(workspace_root=workspace, run_id=run_id)

    path = lifecycle_service.application_lifecycle_path(workspace)
    with lifecycle_service._application_lifecycle_lock(path):
        latest = lifecycle_service.load_application_lifecycle(workspace)
        revision = latest.active_formal_revision if latest else None
        if revision is None or revision.change_id != active.change_id:
            return latest
        if any(lifecycle_service.execution_belongs_to_active_revision(latest, execution)
               for execution in latest.active_executions.values()):
            return latest
        updates = {"active_formal_revision": None, "pending_revision_impact": None, "error": None}
        if latest.active_run_id == run_id:
            updates["active_run_id"] = None
        if run_id:
            updates["resource_locks"] = lifecycle_service._resource_locks_without_run(latest.resource_locks, run_id)
        # 返回设计借用的是初始化展示；结束修订后恢复应用已经创建完成的事实。
        updates["initialization"] = latest.initialization.model_copy(update={
            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            "status": ApplicationLifecycleStatus.COMPLETED,
        })
        return lifecycle_service.write_application_lifecycle(workspace, latest.model_copy(update={
            **updates, "revision": latest.revision + 1, "updated_at": lifecycle_service.utc_now(),
        }), expected_revision=latest.revision)
