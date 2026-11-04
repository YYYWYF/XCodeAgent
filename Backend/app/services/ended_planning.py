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


def restore_ended_planning(workspace, current, *, source_run_id, thread_id):
    """在既有 lifecycle CAS 锁内核验并在内存恢复；新 Run 成功登记时才消费凭据。"""
    from app.services.application_lifecycle import ApplicationLifecycleConflictError

    if not ended_planning_available(workspace, current, run_id=source_run_id, thread_id=thread_id):
        raise ApplicationLifecycleConflictError("工作区或当前任务已变化，不能继续原规划，请在开发阶段重新提出修改。")
    archived = current.ended_planning_execution
    return current.model_copy(update={
        "initialization": archived.initialization,
        "active_formal_revision": archived.formal_revision,
        "ended_planning_execution": None,
    })
