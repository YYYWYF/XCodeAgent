"""把已经解析的恢复事实投影为 Application Planning 公共协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.application_planning_recovery import parse_application_planning_boundary
from app.domain.execution_recovery import DurableExecutionRecord, DurableExecutionStatus
from app.protocols.application_planning_interrupt import (
    application_planning_interrupt_from_snapshot,
)
from app.services.execution_recovery_projection import recovery_failure_diagnostic


@dataclass(frozen=True)
class ApplicationPlanningRecoveryProjection:
    """保存 application-planning-recovery.v1 的稳定公开字段。"""

    classification: str
    source_run_id: str | None
    thread_id: str
    can_continue: bool
    user_action_required: bool
    input_committed: bool
    reason_code: str
    message: str
    failure_diagnostic: dict[str, object] | None = None
    recovery_action_plan: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        """输出保持兼容且不泄漏 checkpoint authority 的 camelCase DTO。"""

        return {
            "schemaVersion": "application-planning-recovery.v1",
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


def build_application_planning_recovery_projection(
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
    """只把上游已经确定的恢复结果装配成公共 DTO。"""

    return ApplicationPlanningRecoveryProjection(
        classification=classification,
        source_run_id=source.run_id if source else None,
        thread_id=thread_id,
        can_continue=can_continue,
        user_action_required=user_action_required,
        input_committed=input_committed,
        reason_code=reason_code,
        message=message,
        failure_diagnostic=(recovery_failure_diagnostic(source) if source else None),
        recovery_action_plan=recovery_action_plan,
    )


def application_planning_input_committed(
    source: DurableExecutionRecord | None,
    snapshot: Any,
) -> bool:
    """读取仅供 UI 展示的已提交输入事实，不参与恢复 eligibility。"""

    values = getattr(snapshot, "values", {})
    values = values if isinstance(values, dict) else {}
    if source is None or str(values.get("active_run_id") or "").strip() != source.run_id:
        return False
    boundary = parse_application_planning_boundary(
        values.get("application_planning_recovery_boundary")
    )
    if boundary is not None:
        return boundary.boundary.value == "input_committed"
    interaction = values.get("application_planning_interaction")
    if not isinstance(interaction, dict):
        return False
    answers = interaction.get("answers")
    request = str(interaction.get("request") or "").strip()
    return bool(
        interaction.get("action") == "answer"
        and interaction.get("artifact") == "requirement_spec"
        and str(interaction.get("gate_id") or "").strip()
        and str(interaction.get("artifact_revision") or "").strip()
        and ((isinstance(answers, dict) and answers) or request)
        and application_planning_interrupt_from_snapshot(snapshot) is None
    )


def project_application_planning_recovery_snapshot(
    values: dict[str, Any],
    *,
    projection: ApplicationPlanningRecoveryProjection,
) -> dict[str, Any]:
    """纯化地清理过期交互展示并写入公共恢复投影。"""

    result = dict(values)
    result["applicationPlanningRecovery"] = projection.to_payload()
    if projection.classification == "awaiting_user":
        return result
    result.pop("application_planning_interrupt", None)
    result.pop("clarification", None)
    if projection.classification in {"running", "completed"}:
        result["status"] = projection.classification
    else:
        result["status"] = "failed"
    result["message"] = projection.message
    return result


def terminal_application_planning_projection_fields(
    status: DurableExecutionStatus,
) -> tuple[str, str, bool]:
    """把已对账的 Durable terminal status 转换为公开展示字段。"""

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
