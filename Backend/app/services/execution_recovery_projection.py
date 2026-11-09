"""把 Durable Recovery 内部判断转换为 lifecycle GET 的安全运行时投影。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionRecoveryProjection,
    ExecutionRecoveryProjectionCandidate,
)
from app.graph.application_planning_workflow import application_planning_graph_for_request
from app.graph.workflow import workflow_graph_for_request
from app.persistence.execution_recovery import (
    list_recovery_projection_candidates,
)
from app.services.execution_failure_classifier import public_failure_diagnostic
from app.services.execution_recovery import recovery_graph_for_execution
from app.services.application_lifecycle import load_application_lifecycle
from app.services.recovery_read_diagnostic import recovery_read_error, recovery_read_operation


logger = logging.getLogger("uvicorn.error")


def recovery_failure_diagnostic(
    record: DurableExecutionRecord,
) -> dict[str, object] | None:
    """把内部失败证据映射为稳定的 Recovery 公共诊断 DTO。"""

    # 展示可补充当前同一规划 Run 的原因，但绝不修改 Durable 失败身份或恢复动作。
    if record.execution_kind == "workbench" and record.status is DurableExecutionStatus.FAILED:
        from app.services.planning_failure_diagnostic import planning_failure_evidence
        from app.services.planning_run_contracts import PlanningRunProjection
        from app.workspace.planning_run_documents import load_planning_run

        try:
            snapshot = load_planning_run({"workspace": record.workspace})
            planning_failure = planning_failure_evidence(
                PlanningRunProjection.model_validate(snapshot),
                source_run_id=record.run_id, source_thread_id=record.thread_id,
            ) if snapshot else None
        except (OSError, ValueError):
            # 可选诊断文件损坏不阻断现有恢复控制面。
            planning_failure = None
        if planning_failure is not None:
            return public_failure_diagnostic(planning_failure, source_run_id=record.run_id)
    if record.failure is not None:
        return public_failure_diagnostic(record.failure, source_run_id=record.run_id)
    # 已处理的业务失败可能没有异常 evidence；只补充同 Run/Thread 生命周期的原始错误用于展示。
    if record.execution_kind == "workbench" and record.status is DurableExecutionStatus.FAILED:
        lifecycle = load_application_lifecycle(record.workspace)
        execution = lifecycle.active_executions.get(record.run_id) if lifecycle else None
        if execution and execution.thread_id == record.thread_id and execution.error:
            return {
                "sourceRunId": record.run_id, "origin": "unknown",
                "code": execution.error.code, "operation": execution.phase,
                "message": execution.error.message,
            }
    return None


async def resolve_execution_recovery_projection(
    workspace: str,
    *,
    active_workbench_run_ids: set[str] | None = None,
) -> ExecutionRecoveryProjection:
    """解析当前工作区可展示的异常终止执行，不创建恢复 child 或执行 Graph。"""

    generated_at = datetime.now(timezone.utc)
    try:
        with recovery_read_operation("查询任务恢复记录"):
            records = await list_recovery_projection_candidates(workspace, limit=16)
    except Exception as exc:
        error = recovery_read_error(exc, operation="查询任务恢复状态")
        logger.warning(
            "recovery.projection.candidates_failed workspace=%s diagnosticId=%s error=%s",
            workspace, error.diagnostic.diagnostic_id,
            error.diagnostic.reason,
            exc_info=True,
        )
        return ExecutionRecoveryProjection(generated_at=generated_at, error=error)

    candidates: list[ExecutionRecoveryProjectionCandidate] = []
    projection_error = None
    for record in records:
        # Workbench 的恢复资格必须仍由 lifecycle 持有；End 移除 execution 后，
        # 即使 durable 失败记录保留作审计，也不能重新投影为可重试卡片。
        if (
            active_workbench_run_ids is not None
            and record.execution_kind == "workbench"
            and record.run_id not in active_workbench_run_ids
        ):
            continue
        try:
            if not await _record_has_current_owner(workspace, record):
                continue
            candidate = await _resolve_candidate(record)
        except Exception as exc:
            error = recovery_read_error(
                exc, operation="解析任务恢复状态",
                source_run_id=record.run_id, thread_id=record.thread_id,
            )
            logger.warning(
                "recovery.projection.candidate_failed workspace=%s runId=%s diagnosticId=%s error=%s",
                workspace,
                record.run_id,
                error.diagnostic.diagnostic_id,
                error.diagnostic.reason,
                exc_info=True,
            )
            # 多条失败保留第一条诊断；其他异常仍以各自编号完整写入日志。
            if projection_error is None:
                projection_error = error
            continue
        if candidate is not None:
            candidates.append(candidate)
    return ExecutionRecoveryProjection(
        generated_at=generated_at,
        candidates=candidates,
        error=projection_error,
    )


async def _record_has_current_owner(workspace: str, record: DurableExecutionRecord) -> bool:
    """只解释当前 owner，历史等待记录不能触发历史解码或覆盖已结束、已交接的线程。"""
    lifecycle = load_application_lifecycle(workspace)
    if lifecycle is None:
        return True
    if record.execution_kind == "workbench":
        return record.run_id in lifecycle.active_executions
    if record.thread_id != lifecycle.initialization.thread_id:
        return False
    if lifecycle.active_run_id:
        return record.run_id == lifecycle.active_run_id
    if lifecycle.ended_planning_execution is not None or str(lifecycle.initialization.status) not in {"failed", "running"}:
        return False
    from app.persistence.execution_recovery import get_latest_execution_for_thread
    latest = await get_latest_execution_for_thread(workspace, thread_id=record.thread_id)
    return latest is not None and latest.run_id == record.run_id


async def reconcile_recovery_ownership(workspace: str) -> None:
    """仅由显式 Workspace Attach 补建索引并收口失效 owner；只读投影不调用这里。"""
    from app.services.checkpoint_execution import index_missing_checkpoint_executions
    await index_missing_checkpoint_executions(workspace)
    for record in await list_recovery_projection_candidates(workspace, limit=16):
        if await _record_has_current_owner(workspace, record):
            await _resolve_candidate(record, reconcile_ownership=True)


async def _resolve_candidate(
    record: DurableExecutionRecord,
    *, reconcile_ownership: bool = False,
) -> ExecutionRecoveryProjectionCandidate | None:
    """为单条记录选择 Graph，FAILED 与 INTERRUPTED 均走各自 authority resolver。"""

    graph_factory = (
        application_planning_graph_for_request
        if record.execution_kind == "application_planning"
        else workflow_graph_for_request
    )
    with recovery_read_operation("构建任务工作流"):
        graph = await recovery_graph_for_execution(record) if record.workflow_scope == "conversation" else await graph_factory(
            workspace=record.workspace,
            project_id=record.project_id,
        )
    from app.services.checkpoint_execution import resolve_checkpoint_source
    with recovery_read_operation("读取任务执行进度"):
        record = await resolve_checkpoint_source(record, graph)
    if record.status not in {DurableExecutionStatus.FAILED, DurableExecutionStatus.INTERRUPTED}:
        return None
    from app.services.business_node_retry import retry_action_plan, release_unretryable_execution
    action_plan = await retry_action_plan(record, graph)
    if reconcile_ownership:
        from app.services.failed_execution_reconciliation import reconcile_failed_execution_lifecycle
        reconcile_failed_execution_lifecycle(record)
        if action_plan.primary_action is None:
            await release_unretryable_execution(record)
    availability = "ready" if action_plan.primary_action else "blocked"
    can_continue = action_plan.primary_action is not None
    owner_session_id = record.owner_session_id
    if not owner_session_id:
        return None
    return ExecutionRecoveryProjectionCandidate(
        source_run_id=record.run_id,
        owner_session_id=owner_session_id,
        thread_id=record.thread_id,
        execution_kind=record.execution_kind,
        workflow_scope=record.workflow_scope,
        execution_status=record.status.value,
        current_node=action_plan.primary_action.target_node if action_plan.primary_action else record.current_node,
        availability=availability,
        can_continue=can_continue,
        reason_code=action_plan.reason_code,
        message=action_plan.message,
        updated_at=record.updated_at,
        failureDiagnostic=recovery_failure_diagnostic(record),
        recoveryActionPlan=action_plan.model_dump(mode="json", by_alias=True),
    )


__all__ = ["recovery_failure_diagnostic", "resolve_execution_recovery_projection"]
