"""把 Durable Recovery 内部判断转换为 lifecycle GET 的安全运行时投影。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionRecoveryProjection,
    ExecutionRecoveryProjectionCandidate,
    RecoveryExecutionError,
)
from app.graph.application_planning_workflow import application_planning_graph_for_request
from app.graph.workflow import workflow_graph_for_request
from app.persistence.execution_recovery import (
    list_recovery_projection_candidates,
)
from app.services.execution_recovery_action_planner import (
    plan_failed_node_reentry_action,
    plan_interrupted_continue_action,
)
from app.services.execution_recovery_reconciliation import (
    reconcile_interrupted_execution_state,
)
from app.services.execution_recovery_source_admission import assess_recovery_source
from app.services.workflow_reentry import FailureTargetResolver, InterruptedTargetResolver


logger = logging.getLogger("uvicorn.error")


def recovery_failure_diagnostic(
    record: DurableExecutionRecord,
) -> dict[str, object] | None:
    """把内部失败证据映射为稳定的 Recovery 公共诊断 DTO。"""

    failure = record.failure
    if failure is None:
        return None
    return {
        "sourceRunId": record.run_id,
        "origin": failure.origin.value,
        "code": failure.code,
        "operation": failure.operation,
        "dependency": failure.dependency,
        "provider": failure.provider,
        "model": failure.model,
        "httpStatus": failure.http_status,
        "message": failure.diagnostic_message,
    }


async def resolve_execution_recovery_projection(
    workspace: str,
) -> ExecutionRecoveryProjection:
    """解析当前工作区可展示的异常终止执行，不创建恢复 child 或执行 Graph。"""

    generated_at = datetime.now(timezone.utc)
    try:
        records = await list_recovery_projection_candidates(workspace, limit=16)
    except Exception as exc:
        logger.warning(
            "recovery.projection.candidates_failed workspace=%s error=%s",
            workspace,
            exc,
            exc_info=True,
        )
        return ExecutionRecoveryProjection(generated_at=generated_at)

    candidates: list[ExecutionRecoveryProjectionCandidate] = []
    for record in records:
        try:
            candidate = await _resolve_candidate(record)
        except Exception as exc:
            logger.warning(
                "recovery.projection.candidate_failed workspace=%s runId=%s error=%s",
                workspace,
                record.run_id,
                exc,
                exc_info=True,
            )
            continue
        if candidate is not None:
            candidates.append(candidate)
    return ExecutionRecoveryProjection(
        generated_at=generated_at,
        candidates=candidates,
    )


async def _resolve_candidate(
    record: DurableExecutionRecord,
) -> ExecutionRecoveryProjectionCandidate | None:
    """为单条记录选择 Graph，FAILED 与 INTERRUPTED 均走各自 authority resolver。"""

    # persistence candidate query 只返回 FAILED/INTERRUPTED；即使未来查询边界漂移，
    # projection 也必须在进入 Generic Recovery 前直接 fail closed。
    if record.status not in {
        DurableExecutionStatus.FAILED,
        DurableExecutionStatus.INTERRUPTED,
    }:
        return None

    graph_factory = (
        application_planning_graph_for_request
        if record.execution_kind == "application_planning"
        else workflow_graph_for_request
    )
    graph = await graph_factory(
        workspace=record.workspace,
        project_id=record.project_id,
    )
    authority_plan = None
    if record.status is DurableExecutionStatus.FAILED:
        admission = assess_recovery_source(record)
        if not admission.admissible:
            action_plan = plan_failed_node_reentry_action(
                workspace=record.workspace,
                source=record,
                error=RecoveryExecutionError(
                    admission.reason_code,
                    "业务 FAILED 缺少 escaped exception evidence，已阻止 RETRY_FAILED_NODE。",
                ),
            )
        else:
            try:
                reentry_plan = await FailureTargetResolver().resolve(
                    workspace=record.workspace,
                    source=record,
                    graph=graph,
                )
            except RecoveryExecutionError as exc:
                action_plan = plan_failed_node_reentry_action(
                    workspace=record.workspace,
                    source=record,
                    error=exc,
                )
            else:
                action_plan = plan_failed_node_reentry_action(
                    workspace=record.workspace,
                    source=record,
                    reentry_plan=reentry_plan,
                )
                authority_plan = reentry_plan
    elif record.status is DurableExecutionStatus.INTERRUPTED:
        resolution = await InterruptedTargetResolver().resolve(
            workspace=record.workspace,
            source=record,
            graph=graph,
        )
        if resolution.kind == "terminal":
            await reconcile_interrupted_execution_state(
                workspace=record.workspace,
                source=record,
                status=resolution.terminal_status or DurableExecutionStatus.COMPLETED,
                snapshot=resolution.snapshot,
            )
            return None
        if resolution.kind == "awaiting_user":
            # 原生 interrupt 已经是现有交互的 authority；只对账，不生成恢复 action。
            await reconcile_interrupted_execution_state(
                workspace=record.workspace,
                source=record,
                status=resolution.terminal_status or DurableExecutionStatus.AWAITING_USER,
                snapshot=resolution.snapshot,
            )
            return None
        if resolution.kind == "continue" and resolution.reentry_plan is not None:
            action_plan = plan_interrupted_continue_action(
                workspace=record.workspace,
                source=record,
                reentry_plan=resolution.reentry_plan,
            )
            authority_plan = resolution.reentry_plan
        else:
            action_plan = plan_interrupted_continue_action(
                workspace=record.workspace,
                source=record,
                error=RecoveryExecutionError(
                    resolution.reason_code,
                    resolution.reason,
                ),
            )
    availability = {
        "recoverable": "ready",
        "awaiting_user": "awaiting_user",
        "needs_attention": "blocked",
    }[action_plan.status.value]
    owner_session_id = await _resolve_owner_session_id(
        record=record,
        plan=authority_plan,
        graph=graph,
    )
    if owner_session_id is None:
        return None
    can_continue = action_plan.status.value == "recoverable" and action_plan.primary_action is not None
    return ExecutionRecoveryProjectionCandidate(
        source_run_id=record.run_id,
        owner_session_id=owner_session_id,
        thread_id=record.thread_id,
        execution_kind=record.execution_kind,
        workflow_scope=record.workflow_scope,
        execution_status=record.status.value,
        current_node=record.current_node,
        availability=availability,
        can_continue=can_continue,
        reason_code=action_plan.reason_code,
        message=action_plan.message,
        updated_at=record.updated_at,
        failureDiagnostic=recovery_failure_diagnostic(record),
        recoveryActionPlan=action_plan.model_dump(mode="json", by_alias=True),
    )


async def _resolve_owner_session_id(
    *,
    record: DurableExecutionRecord,
    plan: object,
    graph: object,
) -> str | None:
    """优先读取 durable ownership，旧记录仅回查 P0.3A 精确 checkpoint。"""

    owner_session_id = str(record.owner_session_id or "").strip()
    if owner_session_id:
        return owner_session_id
    authority = getattr(plan, "context_authority", None)
    checkpoint_id = getattr(plan, "checkpoint_id", None) or getattr(
        authority, "checkpoint_id", None
    )
    if not checkpoint_id or not hasattr(graph, "aget_state"):
        return None
    config = {
        "configurable": {
            "thread_id": record.thread_id,
            "checkpoint_ns": str(
                getattr(plan, "checkpoint_ns", None)
                or getattr(authority, "checkpoint_ns", "")
                or ""
            ),
            "checkpoint_id": str(checkpoint_id),
        }
    }
    try:
        snapshot = await graph.aget_state(config)
    except Exception:
        return None
    values = getattr(snapshot, "values", {})
    if not isinstance(values, dict):
        return None
    resolved = str(values.get("owner_session_id") or "").strip()
    return resolved or None


__all__ = ["recovery_failure_diagnostic", "resolve_execution_recovery_projection"]
