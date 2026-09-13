"""统一 Recovery Incident 的动作规划层。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    RecoveryAction,
    RecoveryActionKind,
    RecoveryActionPlan,
    RecoveryDecision,
    RecoveryIncidentStatus,
    RecoveryPlan,
    RecoveryPoint,
    execution_failure_sha256,
)
from app.persistence.execution_recovery import list_recovery_attempts_for_thread
from app.services.application_lifecycle import ApplicationLifecycle, load_application_lifecycle
from app.services.application_planning_stage_recovery import (
    ApplicationPlanningStageRecoveryContract,
    TechnicalPlanningStageRestartAssessment,
)


async def plan_recovery_action(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    recovery_plan: RecoveryPlan,
    point: RecoveryPoint | None,
    snapshot: Any,
    lifecycle: ApplicationLifecycle | None = None,
) -> tuple[RecoveryActionPlan, TechnicalPlanningStageRestartAssessment | None]:
    """基于 durable facts 选择最近的确定性恢复入口，不执行任何动作。"""

    current_lifecycle = lifecycle or load_application_lifecycle(workspace)
    stage_assessment: TechnicalPlanningStageRestartAssessment | None = None
    if source.execution_kind == "application_planning":
        stage_assessment = ApplicationPlanningStageRecoveryContract().assess(
            workspace=workspace,
            source=source,
            point=point,
            snapshot=snapshot,
            lifecycle=current_lifecycle,
        )
    previous_native_retry = await _source_was_native_retry(
        workspace=workspace,
        source=source,
    )
    incident_id = _incident_id(source=source, point=point, lifecycle=current_lifecycle)
    if recovery_plan.decision is RecoveryDecision.AWAITING_USER:
        return (
            _action_plan(
                source=source,
                incident_id=incident_id,
                status=RecoveryIncidentStatus.AWAITING_USER,
                reason_code="RECOVERY_AWAITING_USER",
                message="当前执行正在等待用户确认，完成确认后才能继续。",
            ),
            stage_assessment,
        )
    if (
        stage_assessment is not None
        and stage_assessment.available
        and (recovery_plan.decision is not RecoveryDecision.READY_NATIVE or previous_native_retry)
    ):
        action = _action(
            incident_id=incident_id,
            kind=RecoveryActionKind.RESTART_STAGE,
            label="重新执行技术规划",
            description=stage_assessment.reason,
        )
        return (
            _action_plan(
                source=source,
                incident_id=incident_id,
                status=RecoveryIncidentStatus.RECOVERABLE,
                reason_code=stage_assessment.reason_code,
                message="当前 checkpoint 无法继续时，已验证正式产物，可从 Technical Planning 阶段重新执行。",
                primary_action=action,
            ),
            stage_assessment,
        )
    if recovery_plan.decision is RecoveryDecision.READY_NATIVE:
        action = _action(
            incident_id=incident_id,
            kind=RecoveryActionKind.CONTINUE_CHECKPOINT,
            label="继续执行",
            description="从已验证的安全 checkpoint 继续执行，并使用当前模型配置。",
        )
        return (
            _action_plan(
                source=source,
                incident_id=incident_id,
                status=RecoveryIncidentStatus.RECOVERABLE,
                reason_code=recovery_plan.reason_code,
                message="已找到可验证的恢复入口，可以继续执行。",
                primary_action=action,
            ),
            stage_assessment,
        )
    return (
        _action_plan(
            source=source,
            incident_id=incident_id,
            status=RecoveryIncidentStatus.NEEDS_ATTENTION,
            reason_code=recovery_plan.reason_code,
            message="当前现场没有可证明安全的自动恢复入口，需要人工处理。",
        ),
        stage_assessment,
    )


def _action_plan(
    *,
    source: DurableExecutionRecord,
    incident_id: str,
    status: RecoveryIncidentStatus,
    reason_code: str,
    message: str,
    primary_action: RecoveryAction | None = None,
) -> RecoveryActionPlan:
    """集中构造严格的当前 RecoveryActionPlan。"""

    return RecoveryActionPlan(
        incidentId=incident_id,
        sourceRunId=source.run_id,
        threadId=source.thread_id,
        executionKind=source.execution_kind,
        status=status,
        reasonCode=reason_code,
        message=message,
        primaryAction=primary_action,
        updatedAt=datetime.now(timezone.utc),
    )


def _action(
    *,
    incident_id: str,
    kind: RecoveryActionKind,
    label: str,
    description: str,
) -> RecoveryAction:
    """根据 incident 和动作类型生成幂等 actionId。"""

    action_id = _digest({"incidentId": incident_id, "kind": kind.value})
    return RecoveryAction(
        actionId=f"recovery-action-{action_id[:32]}",
        kind=kind,
        label=label,
        description=description,
    )


def _incident_id(
    *,
    source: DurableExecutionRecord,
    point: RecoveryPoint | None,
    lifecycle: ApplicationLifecycle | None,
) -> str:
    """只用 canonical source/failure/point/lifecycle revision 生成稳定 incidentId。"""

    return f"recovery-incident-{_digest({
        'sourceRunId': source.run_id,
        'threadId': source.thread_id,
        'failure': execution_failure_sha256(source.failure),
        'recoveryPointId': point.recovery_point_id if point else None,
        'lifecycleRevision': lifecycle.revision if lifecycle else None,
    })[:32]}"


async def _source_was_native_retry(
    *,
    workspace: str,
    source: DurableExecutionRecord,
) -> bool:
    """识别当前 source 是否已经由 Native checkpoint retry 产生，触发下一层降级。"""

    attempts = await list_recovery_attempts_for_thread(
        workspace,
        thread_id=source.thread_id,
    )
    return any(
        attempt.new_run_id == source.run_id
        and attempt.strategy.value == "native_checkpoint"
        for attempt in attempts
    )


def _digest(value: dict[str, Any]) -> str:
    """对动作身份做稳定 canonical SHA-256 摘要。"""

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["plan_recovery_action"]
