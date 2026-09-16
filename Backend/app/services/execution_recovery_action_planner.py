"""统一 Recovery Incident 的动作规划层。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryAction,
    RecoveryActionKind,
    RecoveryActionPlan,
    RecoveryExecutionError,
    RecoveryIncidentStatus,
    WorkflowReentryPlan,
    WorkflowReentryReason,
    execution_failure_sha256,
)
from app.services.application_lifecycle import ApplicationLifecycle, load_application_lifecycle


def plan_failed_node_reentry_action(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    reentry_plan: WorkflowReentryPlan | None = None,
    error: RecoveryExecutionError | None = None,
) -> RecoveryActionPlan:
    """把 WorkflowReentryPlan 或其 fail-closed 错误投影为 FAILED action。"""

    if source.status is DurableExecutionStatus.FAILED and source.failure is None:
        # 业务节点写下的 status=failed 不是 escaped exception；没有异常证据时
        # 即使调用方误传了 reentry_plan，也不能签发 RETRY_FAILED_NODE。
        error = RecoveryExecutionError(
            "FAILED_EXCEPTION_EVIDENCE_MISSING",
            "业务 FAILED 缺少 escaped exception evidence，已阻止 RETRY_FAILED_NODE。",
        )
    lifecycle = load_application_lifecycle(workspace)
    incident_id = _incident_id(
        source=source,
        lifecycle=lifecycle,
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


def plan_interrupted_continue_action(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    reentry_plan: WorkflowReentryPlan | None = None,
    error: RecoveryExecutionError | None = None,
) -> RecoveryActionPlan:
    """把 INTERRUPTED 的最新 checkpoint 解析结果投影为 continue action。"""

    lifecycle = load_application_lifecycle(workspace)
    incident_id = _incident_id(
        source=source,
        lifecycle=lifecycle,
        reentry_plan=reentry_plan,
        reentry_error_code=error.code if error is not None else None,
    )
    if error is not None:
        return _action_plan(
            source=source,
            incident_id=incident_id,
            status=RecoveryIncidentStatus.NEEDS_ATTENTION,
            reason_code=error.code,
            message="中断现场缺少唯一、最新且可验证的 checkpoint，已阻止降级恢复。",
        )
    if (
        reentry_plan is None
        or reentry_plan.reason is not WorkflowReentryReason.INTERRUPTED_CONTINUE
    ):
        return _action_plan(
            source=source,
            incident_id=incident_id,
            status=RecoveryIncidentStatus.NEEDS_ATTENTION,
            reason_code="WORKFLOW_REENTRY_PLAN_INVALID",
            message="中断现场缺少可执行的 Workflow Re-entry 计划，已阻止降级恢复。",
        )
    action = _action(
        incident_id=incident_id,
        kind=RecoveryActionKind.CONTINUE_CHECKPOINT,
        label="继续执行",
        description="从最新已验证的 checkpoint 继续执行未完成步骤。",
    )
    return _action_plan(
        source=source,
        incident_id=incident_id,
        status=RecoveryIncidentStatus.RECOVERABLE,
        reason_code="INTERRUPTED_CONTINUE_READY",
        message="已验证中断执行的最新 checkpoint，可以继续执行。",
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
    lifecycle: ApplicationLifecycle | None,
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
        'lifecycleRevision': lifecycle.revision if lifecycle else None,
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
    "plan_failed_node_reentry_action",
    "plan_interrupted_continue_action",
]
