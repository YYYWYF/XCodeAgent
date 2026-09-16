"""Application Planning 多套恢复事实的唯一解释与状态对账层。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.application_lifecycle import (
    ApplicationLifecycle,
    ApplicationLifecycleStatus,
)
from app.domain.application_planning_recovery import parse_application_planning_boundary
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryExecutionError,
    RecoveryPoint,
)
from app.persistence.execution_recovery import (
    list_recovery_points,
)
from app.protocols.application_planning_interrupt import (
    application_planning_interrupt_from_snapshot,
)
from app.services.execution_recovery import capture_recovery_point
from app.services.execution_recovery_lineage import (
    RecoveryLineageResolution,
    RecoveryLineageState,
)
from app.services.execution_recovery_reconciliation import (
    reconcile_interrupted_execution_state,
)
from app.services.execution_recovery_source_admission import assess_recovery_source
from app.services.execution_recovery_action_planner import (
    plan_failed_node_reentry_action,
    plan_interrupted_continue_action,
)
from app.services.workflow_reentry import FailureTargetResolver, InterruptedTargetResolver


@dataclass(frozen=True)
class ApplicationPlanningRecoveryProjection:
    """保存 Application Planning 恢复页允许公开的稳定事实。"""

    schema_version: str
    classification: str
    source_run_id: str | None
    thread_id: str
    can_continue: bool
    user_action_required: bool
    input_committed: bool
    reason_code: str
    message: str
    failure_diagnostic: dict[str, Any] | None = None
    recovery_action_plan: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        """把内部字段转换为不泄漏 checkpoint authority 的公开 camelCase 结构。"""

        return {
            "schemaVersion": self.schema_version,
            "classification": self.classification,
            "sourceRunId": self.source_run_id,
            "threadId": self.thread_id,
            "canContinue": self.can_continue,
            "userActionRequired": self.user_action_required,
            "inputCommitted": self.input_committed,
            "reasonCode": self.reason_code,
            "message": self.message,
            "failureDiagnostic": self.failure_diagnostic,
            "recoveryActionPlan": self.recovery_action_plan,
        }


async def resolve_application_planning_recovery(
    *,
    workspace: str,
    thread_id: str,
    graph: Any,
    snapshot: Any,
    lifecycle: ApplicationLifecycle | None,
    source: DurableExecutionRecord | None,
    lineage_resolution: RecoveryLineageResolution | None = None,
) -> ApplicationPlanningRecoveryProjection:
    """按 Native Interrupt、Durable 与最新 checkpoint authority 解析唯一恢复分类。"""

    interrupt = application_planning_interrupt_from_snapshot(snapshot)
    lineage_source_is_interrupted = bool(
        source is not None and source.status is DurableExecutionStatus.INTERRUPTED
    ) or bool(
        lineage_resolution is not None
        and lineage_resolution.head is not None
        and lineage_resolution.head.status is DurableExecutionStatus.INTERRUPTED
    )
    if interrupt is not None and not lineage_source_is_interrupted:
        return _projection(
            classification="awaiting_user",
            source=source,
            thread_id=thread_id,
            can_continue=False,
            user_action_required=True,
            input_committed=False,
            reason_code="NATIVE_APPLICATION_PLANNING_INTERRUPT",
            message="当前应用规划正在等待你的确认。",
        )

    if lineage_resolution is not None:
        if lineage_resolution.state is RecoveryLineageState.AMBIGUOUS:
            return _projection(
                classification="blocked",
                source=None,
                thread_id=thread_id,
                reason_code=lineage_resolution.reason_code,
                message="当前规划状态无法安全自动恢复，请查看恢复状态。",
            )
        if lineage_resolution.state is RecoveryLineageState.NO_HEAD:
            source = None
        elif lineage_resolution.head is None:
            return _projection(
                classification="blocked",
                source=None,
                thread_id=thread_id,
                reason_code="RECOVERY_LINEAGE_HEAD_MISSING",
                message="当前规划状态无法安全自动恢复，请查看恢复状态。",
            )
        else:
            source = lineage_resolution.head

    input_committed = _input_committed_for_source(source, snapshot)
    snapshot_values = getattr(snapshot, "values", {})
    snapshot_values = snapshot_values if isinstance(snapshot_values, dict) else {}
    technical_boundary = parse_application_planning_boundary(
        snapshot_values.get("application_planning_recovery_boundary")
    )
    committed_transition_candidate = bool(
        source
        and source.status
        in {
            DurableExecutionStatus.INTERRUPTED,
            DurableExecutionStatus.FAILED,
        }
        and (
            input_committed
            or (
                technical_boundary is not None
                and str(snapshot_values.get("active_run_id") or "").strip()
                == source.run_id
                and technical_boundary.boundary.value
                in {
                    "input_committed",
                    "candidate_committed",
                    "artifact_committed",
                }
            )
        )
    )
    lifecycle_status = lifecycle.initialization.status if lifecycle else None
    if (
        lifecycle_status is ApplicationLifecycleStatus.AWAITING_USER
        and not committed_transition_candidate
        and not (
            source is not None
            and source.status is DurableExecutionStatus.INTERRUPTED
        )
    ):
        return _projection(
            classification="conflict",
            source=source,
            thread_id=thread_id,
            reason_code="LIFECYCLE_AWAITING_USER_WITHOUT_NATIVE_INTERRUPT",
            message="当前规划状态需要重新校准。",
            input_committed=input_committed,
        )
    if source is None:
        return _projection(
            classification="legacy_unverified",
            source=None,
            thread_id=thread_id,
            reason_code="DURABLE_APPLICATION_PLANNING_EXECUTION_MISSING",
            message="当前规划现场缺少可验证的执行记录，无法安全自动恢复。",
        )
    if source.status is DurableExecutionStatus.AWAITING_USER:
        return _projection(
            classification="conflict",
            source=source,
            thread_id=thread_id,
            reason_code="DURABLE_AWAITING_USER_WITHOUT_NATIVE_INTERRUPT",
            message="当前规划状态需要重新校准。",
            input_committed=input_committed,
        )
    if source.status is DurableExecutionStatus.RUNNING:
        return _projection(
            classification="running",
            source=source,
            thread_id=thread_id,
            reason_code="DURABLE_APPLICATION_PLANNING_RUNNING",
            message="当前规划仍在运行。",
        )
    if source.status is DurableExecutionStatus.COMPLETED:
        return _projection(
            classification="completed",
            source=source,
            thread_id=thread_id,
            reason_code="DURABLE_APPLICATION_PLANNING_COMPLETED",
            message="当前规划已经完成。",
        )
    admission = assess_recovery_source(source)
    if not admission.admissible:
        return _projection(
            classification="failed" if source.status is DurableExecutionStatus.FAILED else "blocked",
            source=source,
            thread_id=thread_id,
            reason_code=admission.reason_code,
            message="上一次计划执行缺少可证明安全的恢复证据，当前现场不能自动继续。",
            input_committed=input_committed,
        )
    if source.status not in {
        DurableExecutionStatus.INTERRUPTED,
        DurableExecutionStatus.FAILED,
    }:
        return _projection(
            classification="blocked",
            source=source,
            thread_id=thread_id,
            reason_code="DURABLE_APPLICATION_PLANNING_NOT_CONTINUABLE",
            message="当前计划状态无法安全自动恢复，请查看恢复状态。",
            input_committed=input_committed,
        )

    if source.status is DurableExecutionStatus.FAILED:
        try:
            reentry_plan = await FailureTargetResolver().resolve(
                workspace=workspace,
                source=source,
                graph=graph,
            )
        except Exception as exc:
            if not isinstance(exc, RecoveryExecutionError):
                raise
            action_plan = plan_failed_node_reentry_action(
                workspace=workspace,
                source=source,
                error=exc,
            )
        else:
            action_plan = plan_failed_node_reentry_action(
                workspace=workspace,
                source=source,
                reentry_plan=reentry_plan,
            )
    else:
        resolution = await InterruptedTargetResolver().resolve(
            workspace=workspace,
            source=source,
            graph=graph,
        )
        if resolution.kind == "terminal":
            terminal_status = resolution.terminal_status or DurableExecutionStatus.COMPLETED
            reconciled = await reconcile_interrupted_execution_state(
                workspace=workspace,
                source=source,
                status=terminal_status,
                snapshot=resolution.snapshot,
            )
            classification, message, user_action_required = _terminal_projection_fields(
                terminal_status
            )
            return _projection(
                classification=classification,
                source=reconciled or source,
                thread_id=thread_id,
                user_action_required=user_action_required,
                reason_code=resolution.reason_code,
                message=message,
                input_committed=input_committed,
            )
        if resolution.kind == "awaiting_user":
            terminal_status = resolution.terminal_status or DurableExecutionStatus.AWAITING_USER
            reconciled = await reconcile_interrupted_execution_state(
                workspace=workspace,
                source=source,
                status=terminal_status,
                snapshot=resolution.snapshot,
            )
            return _projection(
                classification="awaiting_user",
                source=reconciled or source,
                thread_id=thread_id,
                user_action_required=True,
                reason_code=resolution.reason_code,
                message="当前应用规划正在等待你的确认。",
                input_committed=input_committed,
            )
        if resolution.kind == "continue" and resolution.reentry_plan is not None:
            action_plan = plan_interrupted_continue_action(
                workspace=workspace,
                source=source,
                reentry_plan=resolution.reentry_plan,
            )
        else:
            action_plan = plan_interrupted_continue_action(
                workspace=workspace,
                source=source,
                error=RecoveryExecutionError(
                    resolution.reason_code,
                    resolution.reason,
                ),
            )
    # Action Planner 只投影已解析的 authority，不再经过 generic eligibility 层。
    recovery_action_plan = action_plan.model_dump(mode="json", by_alias=True)
    if action_plan.primary_action is not None:
        return _projection(
            classification="ready_to_continue",
            source=source,
            thread_id=thread_id,
            can_continue=True,
            input_committed=input_committed,
            reason_code=action_plan.reason_code,
            message=action_plan.message,
            recovery_action_plan=recovery_action_plan,
        )
    return _projection(
        classification="blocked",
        source=source,
        thread_id=thread_id,
        input_committed=input_committed,
        reason_code=action_plan.reason_code,
        message=action_plan.message,
        recovery_action_plan=recovery_action_plan,
    )


def _terminal_projection_fields(
    status: DurableExecutionStatus,
) -> tuple[str, str, bool]:
    """把 Durable terminal status 映射为 Planning projection，不把失败伪装成完成。"""

    return {
        DurableExecutionStatus.COMPLETED: ("completed", "当前规划已经完成。", False),
        DurableExecutionStatus.AWAITING_USER: (
            "awaiting_user",
            "当前应用规划正在等待你的确认。",
            True,
        ),
        DurableExecutionStatus.FAILED: ("failed", "当前规划执行失败。", False),
        DurableExecutionStatus.CANCELLED: ("cancelled", "当前规划执行已取消。", False),
        DurableExecutionStatus.STOPPED: ("stopped", "当前规划执行已停止。", False),
    }[status]


async def ensure_application_planning_recovery_point(
    *,
    source: DurableExecutionRecord,
    graph: Any,
    snapshot: Any,
) -> RecoveryPoint | None:
    """只为通过 source admission 的真实活跃 checkpoint 幂等补写恢复索引。"""

    if not assess_recovery_source(source).admissible:
        return None
    values = getattr(snapshot, "values", {})
    values = values if isinstance(values, dict) else {}
    if str(values.get("active_run_id") or "").strip() != source.run_id:
        return None
    config = getattr(snapshot, "config", {})
    config = config if isinstance(config, dict) else {}
    configurable = config.get("configurable")
    configurable = configurable if isinstance(configurable, dict) else {}
    checkpoint_id = str(configurable.get("checkpoint_id") or "").strip()
    checkpoint_ns = str(configurable.get("checkpoint_ns") or "")
    next_nodes = [str(node) for node in (getattr(snapshot, "next", ()) or ())]
    if not checkpoint_id or not next_nodes:
        return None
    for point in await list_recovery_points(source.workspace, source.run_id):
        if point.checkpoint_id == checkpoint_id and point.checkpoint_ns == checkpoint_ns:
            return point
    return await capture_recovery_point(
        graph=graph,
        config=config,
        workspace=source.workspace,
        thread_id=source.thread_id,
        run_id=source.run_id,
        workflow_scope=source.workflow_scope,
        completed_node=None,
        first_node=source.first_node,
        snapshot=snapshot,
    )


def sanitize_application_planning_recovery_result(
    values: dict[str, Any],
    *,
    projection: ApplicationPlanningRecoveryProjection,
) -> dict[str, Any]:
    """移除非 actionable 快照的交互能力，同时保留既有业务产物。"""

    result = dict(values)
    result["applicationPlanningRecovery"] = projection.to_payload()
    if projection.classification == "awaiting_user":
        return result
    result.pop("application_planning_interrupt", None)
    # clarification 仍保存在 LangGraph checkpoint；公开恢复快照不携带旧交互展示，
    # 避免任何 UI 消费者绕过 Recovery Projection 把历史数据重新画成当前卡片。
    result.pop("clarification", None)
    if projection.classification == "running":
        result["status"] = "running"
    elif projection.classification == "completed":
        result["status"] = "completed"
    else:
        result["status"] = "failed"
    result["message"] = projection.message
    return result


def _input_committed_for_source(
    source: DurableExecutionRecord | None,
    snapshot: Any,
) -> bool:
    """用 Durable source 与 checkpoint identity 判断回答是否已经提交。"""

    values = getattr(snapshot, "values", {})
    values = values if isinstance(values, dict) else {}
    if not source:
        return False
    if str(values.get("active_run_id") or "").strip() != source.run_id:
        return False
    boundary = values.get("application_planning_recovery_boundary")
    parsed_boundary = parse_application_planning_boundary(boundary)
    if parsed_boundary is not None:
        return parsed_boundary.boundary.value == "input_committed"
    return bool(
        str(values.get("active_run_id") or "").strip() == source.run_id
        and _application_planning_committed_input(snapshot)
        and application_planning_interrupt_from_snapshot(snapshot) is None
    )


def _application_planning_committed_input(snapshot: Any) -> bool:
    """判断 checkpoint 是否持久保存了完整的 Requirement 澄清回答。"""

    values = getattr(snapshot, "values", {})
    values = values if isinstance(values, dict) else {}
    interaction = values.get("application_planning_interaction")
    if not isinstance(interaction, dict):
        return False
    answers = interaction.get("answers")
    request = str(interaction.get("request") or "").strip()
    return (
        interaction.get("action") == "answer"
        and interaction.get("artifact") == "requirement_spec"
        and bool(str(interaction.get("gate_id") or "").strip())
        and bool(str(interaction.get("artifact_revision") or "").strip())
        and ((isinstance(answers, dict) and bool(answers)) or bool(request))
    )


def _projection(
    *,
    classification: str,
    source: DurableExecutionRecord | None,
    thread_id: str,
    reason_code: str,
    message: str,
    can_continue: bool = False,
    user_action_required: bool = False,
    input_committed: bool = False,
    recovery_action_plan: dict[str, Any] | None = None,
) -> ApplicationPlanningRecoveryProjection:
    """集中构造 v1 投影并固定所有布尔默认值。"""

    return ApplicationPlanningRecoveryProjection(
        schema_version="application-planning-recovery.v1",
        classification=classification,
        source_run_id=source.run_id if source else None,
        thread_id=thread_id,
        can_continue=can_continue,
        user_action_required=user_action_required,
        input_committed=input_committed,
        reason_code=reason_code,
        message=message,
        failure_diagnostic=_failure_diagnostic(source),
        recovery_action_plan=recovery_action_plan,
    )


def _failure_diagnostic(
    source: DurableExecutionRecord | None,
) -> dict[str, Any] | None:
    """从已经解析出的 canonical head 转换安全失败诊断公开结构。"""

    if source is None or source.failure is None:
        return None
    failure = source.failure
    return {
        "sourceRunId": source.run_id,
        "origin": failure.origin.value,
        "code": failure.code,
        "operation": failure.operation,
        "dependency": failure.dependency,
        "provider": failure.provider,
        "model": failure.model,
        "httpStatus": failure.http_status,
        "message": failure.diagnostic_message,
    }


__all__ = [
    "ApplicationPlanningRecoveryProjection",
    "ensure_application_planning_recovery_point",
    "resolve_application_planning_recovery",
    "sanitize_application_planning_recovery_result",
]
