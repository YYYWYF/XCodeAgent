"""把 Durable Recovery 内部判断转换为 lifecycle GET 的安全运行时投影。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    ExecutionRecoveryProjection,
    ExecutionRecoveryProjectionCandidate,
    RecoveryDecision,
)
from app.graph.application_planning_workflow import application_planning_graph_for_request
from app.graph.workflow import workflow_graph_for_request
from app.persistence.execution_recovery import list_recovery_projection_candidates
from app.services.execution_recovery_coordinator import prepare_continue


logger = logging.getLogger("uvicorn.error")


async def resolve_execution_recovery_projection(
    workspace: str,
) -> ExecutionRecoveryProjection:
    """只读解析当前工作区可展示的中断执行，不创建恢复 child 或修改业务状态。"""

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
    """为单条中断记录选择正确 Graph 并复用 P0.3A 的只读判断。"""

    graph_factory = (
        application_planning_graph_for_request
        if record.execution_kind == "application_planning"
        else workflow_graph_for_request
    )
    graph = await graph_factory(
        workspace=record.workspace,
        project_id=record.project_id,
    )
    plan = await prepare_continue(
        workspace=record.workspace,
        source_run_id=record.run_id,
        graph=graph,
    )
    availability = _availability_for_decision(plan.decision)
    if availability is None:
        return None
    owner_session_id = await _resolve_owner_session_id(
        record=record,
        plan=plan,
        graph=graph,
    )
    if owner_session_id is None:
        return None
    can_continue = availability == "ready"
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
        reason_code=plan.reason_code,
        message=_message_for_availability(availability),
        updated_at=record.updated_at,
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
    checkpoint_id = getattr(plan, "checkpoint_id", None)
    if not checkpoint_id or not hasattr(graph, "aget_state"):
        return None
    config = {
        "configurable": {
            "thread_id": record.thread_id,
            "checkpoint_ns": str(getattr(plan, "checkpoint_ns", "") or ""),
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


def _availability_for_decision(
    decision: RecoveryDecision,
) -> str | None:
    """把 P0.3A 决策映射为不暴露内部恢复 authority 的 UI 状态。"""

    if decision is RecoveryDecision.READY_NATIVE:
        return "ready"
    if decision is RecoveryDecision.REQUIRES_HANDLER:
        return "requires_handler"
    if decision in {
        RecoveryDecision.STATE_DRIFT,
        RecoveryDecision.INVALID_RECOVERY_POINT,
    }:
        return "blocked"
    if decision is RecoveryDecision.AWAITING_USER:
        return "awaiting_user"
    return None


def _message_for_availability(availability: str) -> str:
    """生成面向普通用户的恢复文案，隐藏节点和技术错误码。"""

    return {
        "ready": "上一次执行被中断，可以从已保存的现场继续。",
        "requires_handler": "当前步骤暂不能自动继续。",
        "blocked": "工作区或流程状态已经发生变化，无法直接从旧现场继续。",
        "awaiting_user": "当前执行正在等待用户确认。",
    }[availability]


__all__ = ["resolve_execution_recovery_projection"]
