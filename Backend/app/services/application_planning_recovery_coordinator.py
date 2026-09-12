"""Application Planning 多套恢复事实的唯一只读解释层。"""

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
    RecoveryDecision,
    RecoveryPoint,
)
from app.persistence.execution_recovery import (
    get_recovery_point,
    list_recovery_points,
)
from app.protocols.application_planning_interrupt import (
    application_planning_interrupt_from_snapshot,
)
from app.services.application_planning_recovery_policy import (
    application_planning_committed_input,
)
from app.services.application_planning_recovery_contracts import (
    resolve_application_planning_recovery_contract,
)
from app.services.execution_recovery import capture_recovery_point
from app.services.execution_recovery_coordinator import prepare_continue
from app.services.execution_recovery_policies import (
    production_recovery_replay_policies,
)


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
        }


async def resolve_application_planning_recovery(
    *,
    workspace: str,
    thread_id: str,
    graph: Any,
    snapshot: Any,
    lifecycle: ApplicationLifecycle | None,
    source: DurableExecutionRecord | None,
) -> ApplicationPlanningRecoveryProjection:
    """按 Native Interrupt、Durable、索引和策略顺序解析唯一恢复分类。"""

    interrupt = application_planning_interrupt_from_snapshot(snapshot)
    if interrupt is not None:
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

    input_committed = _input_committed_for_source(source, snapshot)
    snapshot_values = getattr(snapshot, "values", {})
    snapshot_values = snapshot_values if isinstance(snapshot_values, dict) else {}
    technical_boundary = parse_application_planning_boundary(
        snapshot_values.get("application_planning_recovery_boundary")
    )
    committed_transition_candidate = bool(
        source
        and source.status is DurableExecutionStatus.INTERRUPTED
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
    if source.status is DurableExecutionStatus.FAILED:
        return _projection(
            classification="failed",
            source=source,
            thread_id=thread_id,
            reason_code="DURABLE_APPLICATION_PLANNING_FAILED",
            message="上一次规划执行失败，当前现场不能安全自动继续。",
            input_committed=input_committed,
        )
    if source.status is not DurableExecutionStatus.INTERRUPTED:
        return _projection(
            classification="blocked",
            source=source,
            thread_id=thread_id,
            reason_code="DURABLE_APPLICATION_PLANNING_NOT_CONTINUABLE",
            message="当前规划状态无法安全自动恢复，请查看恢复状态。",
            input_committed=input_committed,
        )

    await ensure_application_planning_recovery_point(
        source=source,
        graph=graph,
        snapshot=snapshot,
    )
    plan = await prepare_continue(
        workspace=workspace,
        source_run_id=source.run_id,
        graph=graph,
        replay_policies=production_recovery_replay_policies(),
    )
    if plan.decision is RecoveryDecision.READY_NATIVE:
        contract = resolve_application_planning_recovery_contract(
            source=source,
            point=await _recovery_point_for_plan(source, plan),
            snapshot=snapshot,
        )
        return _projection(
            classification="ready_to_continue",
            source=source,
            thread_id=thread_id,
            can_continue=True,
            input_committed=input_committed,
            reason_code=plan.reason_code,
            message=(
                contract.recovery_message()
                if contract is not None
                else "上一次规划执行被中断，可以继续执行。"
            ),
        )
    if plan.decision is RecoveryDecision.AWAITING_USER:
        return _projection(
            classification="conflict",
            source=source,
            thread_id=thread_id,
            input_committed=input_committed,
            reason_code="UNTYPED_INTERRUPT_CONFLICT",
            message="当前规划状态需要重新校准。",
        )
    return _projection(
        classification="blocked",
        source=source,
        thread_id=thread_id,
        input_committed=input_committed,
        reason_code=plan.reason_code,
        message="当前规划状态无法安全自动恢复，请查看恢复状态。",
    )


async def ensure_application_planning_recovery_point(
    *,
    source: DurableExecutionRecord,
    graph: Any,
    snapshot: Any,
) -> RecoveryPoint | None:
    """只为属于中断 source 的真实活跃 checkpoint 幂等补写恢复索引。"""

    if source.status is not DurableExecutionStatus.INTERRUPTED:
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
        and application_planning_committed_input(snapshot)
        and application_planning_interrupt_from_snapshot(snapshot) is None
    )


async def _recovery_point_for_plan(
    source: DurableExecutionRecord,
    plan: Any,
) -> RecoveryPoint:
    """从当前计划引用恢复点索引，供恢复文案 Contract 解析使用。"""

    point = await _point_by_id(source.workspace, plan.recovery_point_id)
    if point is None:
        raise ValueError("READY_NATIVE 计划缺少可解释的 RecoveryPoint。")
    return point


async def _point_by_id(workspace: str, point_id: str | None) -> RecoveryPoint | None:
    """按恢复计划的稳定 ID 读取 RecoveryPoint。"""

    if not point_id:
        return None
    return await get_recovery_point(workspace, point_id)


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
    )


__all__ = [
    "ApplicationPlanningRecoveryProjection",
    "ensure_application_planning_recovery_point",
    "resolve_application_planning_recovery",
    "sanitize_application_planning_recovery_result",
]
