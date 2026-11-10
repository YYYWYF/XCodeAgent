"""Build 单项及批量任务重试的独立准入与已结束调度上下文准备。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.domain.execution_recovery import DurableExecutionStatus
from app.persistence.execution_recovery import get_execution
from app.services.application_lifecycle import load_application_lifecycle
from app.services.build_task_retry_queue import resolve_task_retry_ids
from app.services.build_task_waiting import awaiting_build_task_retry

_locks: dict[tuple[str, str], asyncio.Lock] = {}


async def load_task_retry_source(request):
    """核验工作区、会话、线程与生命周期当前归属，旧卡片不能控制新的执行。"""
    source = await get_execution(request.workspaceRoot, request.sourceRunId)
    lifecycle = load_application_lifecycle(request.workspaceRoot)
    execution = lifecycle.active_executions.get(request.sourceRunId) if lifecycle else None
    # ended_at 同样记录普通失败，是否已放弃应由当前 lifecycle 归属和终态判断。
    if (not source or source.execution_kind != "workbench" or source.workflow_scope == "conversation"
            or Path(source.workspace).resolve() != Path(request.workspaceRoot).resolve()
            or source.thread_id != request.sourceThreadId or source.owner_session_id != request.sessionId
            or not execution or execution.thread_id != request.sourceThreadId
            or execution.owner_session_id != request.sessionId):
        raise ValueError("原 Build 已不属于当前会话或已被替代，请刷新状态。")
    return source


async def prepare_build_task_retry(request, run_id: str):
    """恢复失败或等待处理的已绑定 Build，只重试选中任务并保留成功结果。"""
    from app.graph.workflow import workflow_graph_for_request
    from app.graph.subgraphs.build import _restore_build_runtime
    from app.services.checkpoint_execution import read_checkpoint_execution, execution_is_live
    from app.services.execution_recovery import observe_execution_started
    from app.services.execution_recovery_executor import (
        NativeRecoveryRuntimeContext, _acquire_workspace_lease, _recovery_observability,
    )
    from app.services.application_lifecycle import (
        handoff_workbench_execution_for_recovery, application_lifecycle_payload,
    )
    from app.services.failed_run_cleanup import require_failed_run_drained, shield_run_cleanup
    from app.services.retry_node_state import retry_node_state
    from app.services.retry_preparation import abandon_prepared_retry
    from app.workspace.task_documents import load_build_execution_record

    key = (str(Path(request.workspaceRoot).resolve()).casefold(), request.sourceRunId)
    async with _locks.setdefault(key, asyncio.Lock()):
        source = await load_task_retry_source(request)
        if execution_is_live(source):
            raise ValueError("原 Build 仍在运行，请使用运行中任务的重试入口。")
        if run_id == source.run_id or await get_execution(source.workspace, run_id):
            raise ValueError("重试请求 runId 已使用，请刷新后重试。")
        graph = await workflow_graph_for_request(workspace=source.workspace, project_id=source.project_id)
        checkpoint = await read_checkpoint_execution(source, graph)
        waiting = bool(checkpoint and awaiting_build_task_retry(checkpoint.snapshot.values))
        if (not checkpoint or checkpoint.node != "build"
                or checkpoint.status not in {DurableExecutionStatus.FAILED, DurableExecutionStatus.AWAITING_USER}
                or (checkpoint.status is DurableExecutionStatus.AWAITING_USER and not waiting)):
            raise ValueError("当前执行不在失败或等待处理的 Build 阶段，不能重试其历史任务。")
        if source.status in {DurableExecutionStatus.CANCELLED, DurableExecutionStatus.STOPPED}:
            raise ValueError("原 Build 已停止，请使用工作流继续入口。")
        source = source.model_copy(update={"status": checkpoint.status, "current_node": "build"})
        await require_failed_run_drained(source.run_id)
        state = dict(checkpoint.snapshot.values)
        state.update(workspace=source.workspace, project_id=source.project_id)
        record = load_build_execution_record(state, source.run_id)
        if not record or not record.get("progress"):
            raise ValueError("缺少已提交的 Build 进度，不能安全重试任务。")
        state = _restore_build_runtime(state, source.run_id, record["progress"], retry_failed_tasks=False)
        retry_ids = resolve_task_retry_ids(state, request.taskId)
        state = retry_node_state(state, "build", planning=False)
        state.update(request=checkpoint.request, resume_from="build", retry_source_run_id=source.run_id,
            build_retry_task_ids=sorted(retry_ids), retry_failed_tasks=False,
            active_thread_id=source.thread_id, owner_session_id=source.owner_session_id,
            workflow_scope=source.workflow_scope or "")
        from app.services.failed_execution_reconciliation import reconcile_failed_execution_lifecycle
        reconcile_failed_execution_lifecycle(source)
        before = load_application_lifecycle(source.workspace)
        lease = None
        handed_off = False
        try:
            lifecycle = handoff_workbench_execution_for_recovery(source.workspace,
                source_run_id=source.run_id, new_run_id=run_id, thread_id=source.thread_id,
                phase="build", expected_lifecycle_revision=before.revision,
                awaiting_build_tasks=waiting)
            handed_off = True
            lease = _acquire_workspace_lease(workspace=source.workspace, source=source,
                new_run_id=run_id, lifecycle=lifecycle)
            child = await observe_execution_started(workspace=source.workspace, project_id=source.project_id,
                thread_id=source.thread_id, run_id=run_id, workflow_scope=source.workflow_scope,
                first_node="build", owner_session_id=source.owner_session_id)
            observability = _recovery_observability(run_id=run_id, thread_id=source.thread_id,
                project_id=source.project_id, workspace=source.workspace)
            state.update(active_run_id=run_id, observability=observability,
                lifecycle=application_lifecycle_payload(lifecycle))
            return NativeRecoveryRuntimeContext(source_execution=source, child_execution=child,
                recovery_plan=SimpleNamespace(target_node="build"), new_run_id=run_id,
                thread_id=source.thread_id, project_id=source.project_id, workspace=source.workspace,
                workflow_scope=source.workflow_scope, graph=graph,
                fork_config={"configurable": {"thread_id": source.thread_id, "checkpoint_ns": "",
                    "checkpoint_id": str(uuid4())}, "metadata": {"run_id": run_id,
                    "thread_id": source.thread_id, "workspace": source.workspace,
                    "selected_skill_names": list(state.get("selected_skill_names") or [])}},
                observation_config={"configurable": {"thread_id": source.thread_id}},
                fork_snapshot=SimpleNamespace(values=state), lifecycle_payload=state["lifecycle"],
                workspace_lease=lease, observability=observability, heartbeat_task=None,
                restart_node=True, retry_lifecycle_before_handoff=before)
        except BaseException:
            if handed_off:
                await shield_run_cleanup(abandon_prepared_retry(workspace=source.workspace, source=source,
                    before=before, new_run_id=run_id, lease=lease))
            raise
