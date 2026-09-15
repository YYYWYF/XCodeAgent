"""统一 Recovery Incident 的动作规划层。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryAction,
    RecoveryActionKind,
    RecoveryActionPlan,
    RecoveryDecision,
    RecoveryExecutionError,
    RecoveryIncidentStatus,
    RecoveryPlan,
    RecoveryPoint,
    WorkflowReentryPlan,
    execution_failure_sha256,
)
from app.persistence.execution_recovery import get_recovery_point
from app.services.application_lifecycle import ApplicationLifecycle, load_application_lifecycle
from app.services.application_planning_stage_recovery import (
    ApplicationPlanningStageRecoveryContract,
    TechnicalPlanningStageRestartAssessment,
)
from app.services.execution_recovery_capability import (
    assess_native_recovery_capability,
)
from app.services.execution_retry_dispatcher import (
    RetryOperationCapability,
    assess_retry_operation,
)
from app.services.workflow_reentry import FailureTargetResolver


@dataclass(frozen=True, slots=True)
class RecoveryFacts:
    """保存 projection 与 execute 共同消费的精确 source facts。"""

    point: RecoveryPoint | None
    snapshot: Any
    lifecycle: ApplicationLifecycle | None


async def build_recovery_facts(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    recovery_plan: RecoveryPlan,
    graph: Any,
    lifecycle: ApplicationLifecycle | None = None,
) -> RecoveryFacts:
    """只按完整 checkpoint identity 读取 facts，禁止按 thread 猜当前快照。"""

    point = (
        await get_recovery_point(workspace, recovery_plan.recovery_point_id)
        if recovery_plan.recovery_point_id
        else None
    )
    if point is not None and (
        point.run_id != source.run_id or point.thread_id != source.thread_id
    ):
        point = None
    snapshot: Any = type("EmptyRecoverySnapshot", (), {"values": {}})()
    if point is not None and point.checkpoint_id and hasattr(graph, "aget_state"):
        config = {
            "configurable": {
                "thread_id": source.thread_id,
                "checkpoint_ns": point.checkpoint_ns,
                "checkpoint_id": point.checkpoint_id,
            }
        }
        try:
            resolved = await graph.aget_state(config)
            if resolved is not None:
                snapshot = resolved
        except Exception:
            # facts 读取失败时保留空快照，让 Stage Restart 仍只依赖正式 authority；
            # Native 是否可执行仍由其自身 finalization revalidation 决定。
            pass
    return RecoveryFacts(
        point=point,
        snapshot=snapshot,
        lifecycle=lifecycle or load_application_lifecycle(workspace),
    )


async def plan_recovery_action(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    recovery_plan: RecoveryPlan,
    point: RecoveryPoint | None,
    snapshot: Any,
    lifecycle: ApplicationLifecycle | None = None,
    graph: Any | None = None,
    reentry_plan: WorkflowReentryPlan | None = None,
) -> tuple[RecoveryActionPlan, TechnicalPlanningStageRestartAssessment | None]:
    """基于 durable facts 选择最近的确定性恢复入口，不执行任何动作。"""

    current_lifecycle = lifecycle or load_application_lifecycle(workspace)
    if source.status is DurableExecutionStatus.FAILED:
        # FAILED 的准入只由 Workflow Node Re-entry authority 决定；旧的
        # RecoveryPlan、ReplayPolicy 和 Native capability 不能成为第二套判断。
        if reentry_plan is None:
            if graph is None:
                return (
                    plan_failed_node_reentry_action(
                        workspace=workspace,
                        source=source,
                        error=RecoveryExecutionError(
                            "NODE_ENTRY_AUTHORITY_MISSING",
                            "当前 production Graph 无法解析失败 Node 的精确入口。",
                        ),
                    ),
                    None,
                )
            try:
                reentry_plan = await FailureTargetResolver().resolve(
                    workspace=workspace,
                    source=source,
                    graph=graph,
                )
            except RecoveryExecutionError as exc:
                return (
                    plan_failed_node_reentry_action(
                        workspace=workspace,
                        source=source,
                        error=exc,
                    ),
                    None,
                )
        return (
            plan_failed_node_reentry_action(
                workspace=workspace,
                source=source,
                reentry_plan=reentry_plan,
            ),
            None,
        )
    native_capability = assess_native_recovery_capability(recovery_plan)
    stage_assessment: TechnicalPlanningStageRestartAssessment | None = None
    if source.execution_kind == "application_planning":
        stage_assessment = ApplicationPlanningStageRecoveryContract().assess(
            workspace=workspace,
            source=source,
            point=point,
            snapshot=snapshot,
            lifecycle=current_lifecycle,
        )
    retry_capability = RetryOperationCapability(
        executable=False,
        handler=None,
        reason_code="RETRY_HANDLER_NOT_AVAILABLE",
        reason="当前失败暂未接入可执行的 operation retry handler。",
    )
    if (
        graph is not None
        and source.execution_kind == "workbench"
        and source.status.value == "failed"
        and not native_capability.executable
    ):
        retry_capability = await assess_retry_operation(
            workspace=workspace,
            source=source,
            graph=graph,
        )
    stage_restart_available = bool(
        stage_assessment is not None
        and stage_assessment.available
        and not native_capability.executable
    )
    incident_id = _incident_id(
        source=source,
        point=point,
        lifecycle=current_lifecycle,
        stage_assessment=stage_assessment if stage_restart_available else None,
        retry_handler=(
            retry_capability.handler if retry_capability.executable else None
        ),
    )
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
        and stage_restart_available
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
    if native_capability.executable:
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
    if retry_capability.executable and retry_capability.handler is not None:
        action = _action(
            incident_id=incident_id,
            kind=RecoveryActionKind.RETRY_OPERATION,
            label=_retry_label(retry_capability.handler),
            description=retry_capability.reason,
        )
        return (
            _action_plan(
                source=source,
                incident_id=incident_id,
                status=RecoveryIncidentStatus.RECOVERABLE,
                reason_code=retry_capability.reason_code,
                message="当前失败操作可以安全重试，具体执行方式由 Backend 决定。",
                primary_action=action,
            ),
            stage_assessment,
        )
    return (
        _action_plan(
            source=source,
            incident_id=incident_id,
            status=RecoveryIncidentStatus.NEEDS_ATTENTION,
            reason_code=(
                native_capability.reason_code
                if recovery_plan.decision is RecoveryDecision.READY_NATIVE
                and not native_capability.executable
                else recovery_plan.reason_code
            ),
            message="当前现场没有可证明安全的自动恢复入口，需要人工处理。",
        ),
        stage_assessment,
    )


def plan_failed_node_reentry_action(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    reentry_plan: WorkflowReentryPlan | None = None,
    error: RecoveryExecutionError | None = None,
) -> RecoveryActionPlan:
    """把 WorkflowReentryPlan 或其 fail-closed 错误投影为 FAILED action。"""

    lifecycle = load_application_lifecycle(workspace)
    incident_id = _incident_id(
        source=source,
        point=None,
        lifecycle=lifecycle,
        stage_assessment=None,
        reentry_plan=reentry_plan,
        reentry_error_code=error.code if error is not None else None,
    )
    if error is not None:
        return _action_plan(
            source=source,
            incident_id=incident_id,
            status=RecoveryIncidentStatus.NEEDS_ATTENTION,
            reason_code=error.code,
            message="失败 Node 的精确入口 authority 缺失或无效，已阻止降级恢复。",
        )
    if reentry_plan is None:
        return _action_plan(
            source=source,
            incident_id=incident_id,
            status=RecoveryIncidentStatus.NEEDS_ATTENTION,
            reason_code="WORKFLOW_REENTRY_PLAN_INVALID",
            message="失败 Node 缺少可执行的 Workflow Re-entry 计划，已阻止降级恢复。",
        )
    action = _action(
        incident_id=incident_id,
        kind=RecoveryActionKind.RETRY_FAILED_NODE,
        label=_failed_node_retry_label(reentry_plan.target_node),
        description="恢复失败 Node 开始前的精确语义 State，并使用当前运行配置重新执行。",
    )
    return _action_plan(
        source=source,
        incident_id=incident_id,
        status=RecoveryIncidentStatus.RECOVERABLE,
        reason_code="FAILED_NODE_REENTRY_READY",
        message="已验证失败 Node 的精确入口，可以保留原业务上下文重新执行。",
        primary_action=action,
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


def _retry_label(handler: str) -> str:
    """把 Backend 内部 retry handler 映射为用户可见动作标签。"""

    return {
        "retry_code_review": "重试代码审查",
        "retry_failed_tasks": "重试失败任务",
    }.get(handler, "重试失败操作")


def _failed_node_retry_label(node: str | None) -> str:
    """把失败节点映射为用户可见的重新执行文案。"""

    normalized = str(node or "").strip()
    if normalized.startswith("technical_planning"):
        return "重新执行技术规划"
    return {
        "build": "重新执行代码生成",
        "code_review": "重新执行代码审查",
    }.get(normalized, "重新执行失败步骤")


def _incident_id(
    *,
    source: DurableExecutionRecord,
    point: RecoveryPoint | None,
    lifecycle: ApplicationLifecycle | None,
    stage_assessment: TechnicalPlanningStageRestartAssessment | None,
    retry_handler: str | None = None,
    reentry_plan: WorkflowReentryPlan | None = None,
    reentry_error_code: str | None = None,
) -> str:
    """把恢复 authority 纳入稳定 incidentId，避免 source 变化复用旧动作。"""

    authority = reentry_plan.context_authority if reentry_plan is not None else None

    return f"recovery-incident-{_digest({
        'sourceRunId': source.run_id,
        'threadId': source.thread_id,
        'failure': execution_failure_sha256(source.failure),
        'failedNode': source.current_node,
        'recoveryPointId': point.recovery_point_id if point else None,
        'lifecycleRevision': lifecycle.revision if lifecycle else None,
        'stageAuthoritySha256': (
            stage_assessment.authority.authority_sha256
            if stage_assessment is not None and stage_assessment.authority is not None
            else None
        ),
        'retryHandler': retry_handler,
        'nodeEntryBoundaryId': authority.boundary_id if authority else None,
        'nodeEntrySourceRunId': authority.source_run_id if authority else None,
        'nodeEntryThreadId': authority.thread_id if authority else None,
        'nodeEntryTargetNode': authority.target_node if authority else None,
        'nodeEntryCheckpointId': authority.checkpoint_id if authority else None,
        'nodeEntryCheckpointNs': authority.checkpoint_ns if authority else None,
        'reentryErrorCode': reentry_error_code,
    })[:32]}"


def _digest(value: dict[str, Any]) -> str:
    """对动作身份做稳定 canonical SHA-256 摘要。"""

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "RecoveryFacts",
    "build_recovery_facts",
    "plan_failed_node_reentry_action",
    "plan_recovery_action",
]
