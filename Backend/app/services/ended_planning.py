"""复用结束任务的内容指纹和原业务节点重试，恢复停止的正式规划。"""

from app.services.ended_workspace import ended_workspace_unchanged


def ended_planning_available(workspace, lifecycle, *, run_id=None, thread_id=None):
    """仅允许无新任务且工作区未变的原规划恢复，不赋予历史会话新的写权限。"""
    archived = lifecycle.ended_planning_execution if lifecycle else None
    return bool(archived and (run_id is None or archived.run_id == run_id)
        and (thread_id is None or archived.initialization.thread_id == thread_id)
        and lifecycle.initialization.thread_id == archived.initialization.thread_id
        and lifecycle.active_run_id is None and lifecycle.active_formal_revision is None
        and lifecycle.pending_revision_impact is None and not lifecycle.active_executions
        and ended_workspace_unchanged(str(workspace), archived.fingerprint))


def initial_planning_restart_available(lifecycle, *, run_id=None, thread_id=None):
    """首次创建可显式按当前文件重新生成；不放行正式修订、已换线程或已有新任务。"""
    from app.domain.application_lifecycle import ApplicationLifecycleStatus

    archived = lifecycle.ended_planning_execution if lifecycle else None
    return bool(archived and archived.formal_revision is None
        and (run_id is None or archived.run_id == run_id)
        and (thread_id is None or archived.initialization.thread_id == thread_id)
        and lifecycle.initialization.thread_id == archived.initialization.thread_id
        and lifecycle.initialization.stage == archived.initialization.stage
        and lifecycle.initialization.status is ApplicationLifecycleStatus.CANCELLED
        and lifecycle.active_run_id is None and lifecycle.active_formal_revision is None
        and lifecycle.pending_revision_impact is None and not lifecycle.active_executions
        and not any(lifecycle.resource_locks.model_dump().values()))


def stopped_planning_feedback_available(workspace, lifecycle, *, run_id=None, thread_id=None):
    """新意见只接管当前已结束规划；正式修订仍要求工作区未变化且无其他占用。"""
    archived = lifecycle.ended_planning_execution if lifecycle else None
    if initial_planning_restart_available(lifecycle, run_id=run_id, thread_id=thread_id):
        return True
    return bool(archived and archived.formal_revision is not None
        and archived.formal_revision.planning_thread_id == archived.initialization.thread_id
        and not any(lifecycle.resource_locks.model_dump().values())
        and ended_planning_available(workspace, lifecycle, run_id=run_id, thread_id=thread_id))


def restore_ended_planning(workspace, current, *, source_run_id, thread_id, restart_initial=False):
    """在既有 lifecycle CAS 锁内核验并在内存恢复；新 Run 成功登记时才消费凭据。"""
    from app.services.application_lifecycle import ApplicationLifecycleConflictError

    available = (initial_planning_restart_available(current, run_id=source_run_id, thread_id=thread_id)
        if restart_initial else ended_planning_available(workspace, current, run_id=source_run_id, thread_id=thread_id))
    if not available:
        raise ApplicationLifecycleConflictError("工作区或当前任务已变化，不能继续原规划，请在开发阶段重新提出修改。")
    archived = current.ended_planning_execution
    return current.model_copy(update={
        "initialization": archived.initialization,
        "active_formal_revision": archived.formal_revision,
        "ended_planning_execution": None,
    })
