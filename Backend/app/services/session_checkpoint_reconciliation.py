"""显式 Attach 从当前会话的本机现场校准索引和归属，不重跑 Graph 或改写 checkpoint。"""

import asyncio
from datetime import datetime
from pathlib import Path

from app.domain.application_lifecycle import ExecutionResourceClaim, WorkbenchExecutionStatus
from app.domain.execution_recovery import DurableExecutionRecord, DurableExecutionStatus, RecoveryExecutionError
from app.persistence.checkpoints import workflow_checkpointer
from app.persistence.execution_recovery import get_execution, get_latest_execution_for_thread, insert_execution, update_execution_status
from app.services import application_lifecycle as lifecycle_service
from app.services.checkpoint_execution import checkpoint_source, interpret_checkpoint
from app.services.recovery_checkpoint import checkpoint_thread_exists

_locks: dict[str, asyncio.Lock] = {}


async def reconcile_session_checkpoint(workspace: str, *, session_id: str, thread_id: str) -> bool | None:
    """只校准本会话 root；None 表示被真实活动任务推迟，不能登记为已校准。"""
    workspace = str(Path(workspace).resolve())
    async with _locks.setdefault(workspace, asyncio.Lock()):
        if not await checkpoint_thread_exists(workspace, thread_id):
            return False
        from app.protocols.workflow.run_control import workflow_run_registry
        from app.services.detached_execution import active_detached_count
        from app.services.ui_design_generation_pool import active_ui_design_count
        if workflow_run_registry.active_run_ids(workspace) or active_detached_count(workspace) or active_ui_design_count(workspace):
            return None
        before = lifecycle_service.load_application_lifecycle(workspace)
        if before is None:
            return False
        # 先读当前 root 的 scope，再选择原 Graph；不通过其他图执行或猜测历史线程。
        saver = await workflow_checkpointer(workspace=workspace)
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        saved = await saver.aget_tuple(config)
        values = saved.checkpoint["channel_values"] if saved else {}
        if (not values or values.get("owner_session_id") != session_id
                or Path(str(values.get("workspace") or "")).resolve() != Path(workspace)
                or (values.get("project_id") and values["project_id"] != before.application.id)
                or values.get("workflow_scope") == "application_planning"):
            return False
        run_id = str(values.get("active_run_id") or "")
        if not run_id or run_id in before.ended_executions:
            return False
        latest = await get_latest_execution_for_thread(workspace, thread_id=thread_id)
        if latest and latest.run_id != run_id:
            # 有更晚的已接受执行时保留交接事实，不能用旧 checkpoint 抢回归属。
            return False
        source = await get_execution(workspace, run_id)
        if source and (source.thread_id != thread_id or source.owner_session_id != session_id
                       or source.status in {DurableExecutionStatus.COMPLETED, DurableExecutionStatus.STOPPED, DurableExecutionStatus.CANCELLED}):
            return False
        at = datetime.fromisoformat(saved.checkpoint["ts"])
        candidate = source or DurableExecutionRecord(run_id=run_id, thread_id=thread_id,
            workspace=workspace, project_id=before.application.id, owner_session_id=session_id,
            execution_kind="workbench", workflow_scope=values.get("workflow_scope") or None,
            first_node=str(values.get("phase") or "workflow_entry"),
            status=DurableExecutionStatus.INTERRUPTED, started_at=at, updated_at=at)
        from app.services.execution_recovery import recovery_graph_for_execution
        graph = await recovery_graph_for_execution(candidate)
        snapshot = await graph.aget_state(config)
        if snapshot.config != saved.config:
            raise RecoveryExecutionError("RECOVERY_SNAPSHOT_CHANGED", "当前会话进度正在变化，请重新同步。")
        interpreted = interpret_checkpoint(candidate, snapshot, graph)
        if interpreted is None:
            return False
        values = interpreted.values
        candidate = checkpoint_source(candidate, interpreted)
        control = values.get("plan_control") or {}
        ended = control.get("status") == "ended" or str(values.get("status")) in {"cancelled", "stopped"}
        if ended:
            return False
        active = before.active_executions.get(run_id)
        if active and (active.thread_id != thread_id or active.owner_session_id != session_id):
            return False
        # 写索引只使用原现场的时间及身份，不把旧记录提升为新的 Run。
        changed = source is None
        if source is None:
            await insert_execution(candidate)
        elif (source.status in {DurableExecutionStatus.RUNNING, DurableExecutionStatus.INTERRUPTED}
                and candidate.status != source.status):
            # 真实工作已退出且现场已到确认门时，收口旧 running 镜像，避免后续 resume 被索引误挡住。
            from app.services.workspace_checkpoint_reconciliation import _run_is_live
            if _run_is_live(workspace, run_id):
                return False
            await update_execution_status(workspace=workspace, run_id=run_id, status=candidate.status)
            changed = True
        if active and candidate.status is DurableExecutionStatus.AWAITING_USER:
            from app.services.build_task_waiting import awaiting_build_task_retry
            from app.services.workspace_checkpoint_reconciliation import _run_is_live

            # 只校准本会话的任务重试等待；已有正式确认或并发归属变化不能被清除。
            if awaiting_build_task_retry(values) and active.pending_interaction is None:
                with lifecycle_service._application_lifecycle_lock(lifecycle_service.application_lifecycle_path(workspace)):
                    current = lifecycle_service.load_application_lifecycle(workspace)
                    if (current is not None and current.revision == before.revision
                            and not _run_is_live(workspace, run_id)
                            and (active.status != WorkbenchExecutionStatus.AWAITING_USER or active.phase != "build")):
                        lifecycle_service.update_workbench_execution(workspace, run_id=run_id,
                            phase="build", status=WorkbenchExecutionStatus.AWAITING_USER)
                        changed = True
        if active or candidate.status == DurableExecutionStatus.COMPLETED:
            return changed
        _restore_workbench_owner(workspace, before, candidate, values, interpreted)
        return True


def _restore_workbench_owner(workspace, before, source, values, interpreted) -> None:
    """沿既有登记及交互签发函数恢复原执行引用，遵守现有资源冲突和明确结束边界。"""
    from app.services.preview_runtime_guard import maintenance_lock
    from app.workspace.task_documents import build_task_plan_lifecycle_lock
    from app.protocols.workflow.lifecycle import _pending_interaction
    from app.services.workspace_checkpoint_reconciliation import _run_is_live
    scope = values.get("build_execution_scope") or {}
    page_id = str(values.get("selectedPageId") or "") or None
    scope_type = str(scope.get("type") or ("page" if page_id else "application"))
    target_id = str(scope.get("targetId") or page_id or "application")
    claims = [ExecutionResourceClaim.model_validate(item) for item in values.get("execution_resource_claims") or []]
    with maintenance_lock, build_task_plan_lifecycle_lock(workspace), lifecycle_service._application_lifecycle_lock(lifecycle_service.application_lifecycle_path(workspace)):
        current = lifecycle_service.load_application_lifecycle(workspace)
        if (current is None or current.revision != before.revision or _run_is_live(workspace, source.run_id)):
            raise RecoveryExecutionError("RECOVERY_SNAPSHOT_CHANGED", "当前会话归属正在变化，请重新同步。")
        lifecycle_service.start_workbench_execution(workspace, scope=scope_type, target_id=target_id,
            page_id=target_id if scope_type == "page" else None, thread_id=source.thread_id,
            run_id=source.run_id, phase=str(values.get("phase") or interpreted.node),
            owner_session_id=source.owner_session_id, resource_claims=claims or None,
            api_contract_id=scope.get("apiContractId"))
        status = {DurableExecutionStatus.AWAITING_USER: WorkbenchExecutionStatus.AWAITING_USER,
                  DurableExecutionStatus.FAILED: WorkbenchExecutionStatus.FAILED}.get(
                      source.status, WorkbenchExecutionStatus.STOPPED)
        pending_type, pending = _pending_interaction(values) if status == WorkbenchExecutionStatus.AWAITING_USER else (None, None)
        lifecycle_service.update_workbench_execution(workspace, run_id=source.run_id,
            phase=str(values.get("phase") or interpreted.node), status=status,
            pending_type=pending_type, pending_payload=pending)
