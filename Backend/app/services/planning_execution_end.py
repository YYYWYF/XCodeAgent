"""结束正式修订的规划执行，保留产物并释放本次规划占用。"""

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.execution_recovery import DurableExecutionStatus
from app.persistence.execution_recovery import get_execution, update_execution_status
from app.services import application_lifecycle as lifecycle_service


async def end_planning_execution(workspace: str, thread_id: str):
    """用户停止即结束当前规划，真实退出后清占用；仅正式修订恢复开发准入。"""
    from app.protocols.workflow.run_control import workflow_run_registry
    from app.workspace.run_lease import workspace_run_leases

    current = lifecycle_service.load_application_lifecycle(workspace)
    if current is None or current.initialization.thread_id != thread_id:
        raise ValueError("当前规划线程与应用不匹配。")
    active = current.active_formal_revision
    if active is not None and active.planning_thread_id != thread_id:
        raise ValueError("当前正式修订不属于该规划线程。")
    run_id = active.planning_run_id if active else current.active_run_id
    if active is None and not run_id and current.initialization.status in {
        ApplicationLifecycleStatus.CANCELLED, ApplicationLifecycleStatus.COMPLETED,
    }:
        return current
    run_id = run_id or current.active_run_id
    source = await get_execution(workspace, run_id) if run_id else None
    # 已交接开发的执行由工作台 End 管理；规划结束不能取消其工作或删除其修订。
    if source is not None and (source.execution_kind != "application_planning" or source.thread_id != thread_id):
        raise ValueError("当前执行已交接，请在所属阶段结束任务。")
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
        # 等待旧 writer 退出期间若已更换阶段或修订，不能清理新任务或发布结束成功。
        if latest is None or latest.initialization.thread_id != thread_id or (
            (revision.change_id if revision else None) != (active.change_id if active else None)
        ) or latest.active_run_id not in {None, current.active_run_id, run_id}:
            raise ValueError("当前规划任务已变化，请同步当前任务。")
        if any(lifecycle_service.execution_belongs_to_active_revision(latest, execution)
               for execution in latest.active_executions.values()):
            raise ValueError("当前修订已进入工作台，请在开发阶段结束任务。")
        updates = {"active_formal_revision": None, "pending_revision_impact": None, "error": None}
        if active is not None and source is not None:
            from app.domain.application_lifecycle import EndedPlanningExecution
            from app.services.ended_workspace import workspace_fingerprint

            # 与开发任务 End 一样，在 writer 真实退出后保存稳定基线，释放占用但保留继续凭据。
            updates["ended_planning_execution"] = EndedPlanningExecution(
                runId=run_id, initialization=latest.initialization,
                formalRevision=revision, fingerprint=workspace_fingerprint(workspace),
                workspace=workspace,
            )
        if latest.active_run_id == run_id:
            updates["active_run_id"] = None
        if run_id:
            updates["resource_locks"] = lifecycle_service._resource_locks_without_run(latest.resource_locks, run_id)
        # 修订返回规划只是借用初始化展示；首次创建未完成时不能跳过正式产物确认。
        updates["initialization"] = latest.initialization.model_copy(update={
            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH if active else latest.initialization.stage,
            "status": ApplicationLifecycleStatus.COMPLETED if active else ApplicationLifecycleStatus.CANCELLED,
        })
        return lifecycle_service.write_application_lifecycle(workspace, latest.model_copy(update={
            **updates, "revision": latest.revision + 1, "updated_at": lifecycle_service.utc_now(),
        }), expected_revision=latest.revision)
