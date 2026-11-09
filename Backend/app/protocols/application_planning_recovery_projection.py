"""把已经解析的恢复事实投影为 Application Planning 公共协议。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.application_planning_recovery import parse_application_planning_boundary
from app.domain.execution_recovery import DurableExecutionRecord, DurableExecutionStatus
from app.domain.application_lifecycle import ApplicationLifecycleStatus
from app.services.application_lifecycle import load_application_lifecycle
from app.services.ui_design_recovery import interrupted_ui_design_pages
from app.services.execution_recovery_lineage import RecoveryLineageResolution
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
    ui_generation_recovery: dict[str, Any] | None = None

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
            "uiGenerationRecovery": self.ui_generation_recovery,
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
    ui_generation_recovery: dict[str, Any] | None = None,
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
        ui_generation_recovery=ui_generation_recovery,
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


async def project_planning_recovery(
    *,
    workspace: str,
    thread_id: str,
    graph: Any,
    snapshot: Any,
    source: DurableExecutionRecord | None,
    lineage_resolution: RecoveryLineageResolution | None = None,
) -> ApplicationPlanningRecoveryProjection:
    """在公开 Workflow 边界调用 Generic Recovery authority 并生成 Planning DTO。"""

    input_committed = application_planning_input_committed(source, snapshot)
    # 只有取消记录与已清锁的结束 lifecycle 同时成立，才是用户主动 End；断连取消不能冒充。
    lifecycle = load_application_lifecycle(workspace)
    archived = lifecycle.ended_planning_execution if lifecycle else None
    if (source is None and archived and archived.run_id == snapshot.values.get("active_run_id")
            and archived.initialization.thread_id == thread_id and lifecycle.active_run_id is None):
        return build_application_planning_recovery_projection(
            classification="completed", source=None, thread_id=thread_id,
            reason_code="APPLICATION_PLANNING_ENDED", message="本轮任务已结束，已有文件已保留。")
    from app.services.ended_planning import ended_planning_available
    if (source is not None and source.status is DurableExecutionStatus.CANCELLED
            and source.ended_at is not None
            and ended_planning_available(workspace, lifecycle, run_id=source.run_id, thread_id=thread_id)):
        from app.services.business_node_retry import retry_action_plan

        # 正常停止仍使用原节点重试签发动作，不恢复停止前的旧确认门。
        plan = await retry_action_plan(source.model_copy(update={"status": DurableExecutionStatus.INTERRUPTED}), graph)
        return build_application_planning_recovery_projection(
            classification="ready_to_continue" if plan.primary_action else "blocked",
            source=source, thread_id=thread_id, input_committed=input_committed,
            reason_code="APPLICATION_PLANNING_ENDED", message="本轮任务已结束，已有文件已保留。",
            can_continue=plan.primary_action is not None,
            recovery_action_plan=plan.model_dump(mode="json", by_alias=True),
        )
    if (source is not None and source.status is DurableExecutionStatus.CANCELLED
            and source.ended_at is not None and lifecycle is not None
            and lifecycle.initialization.thread_id == thread_id
            and lifecycle.active_run_id is None and lifecycle.active_formal_revision is None
            and lifecycle.initialization.status in {
                ApplicationLifecycleStatus.CANCELLED, ApplicationLifecycleStatus.COMPLETED,
            }):
        return build_application_planning_recovery_projection(
            classification="completed", source=source, thread_id=thread_id,
            input_committed=input_committed, reason_code="APPLICATION_PLANNING_ENDED",
            message="本轮任务已结束，已有文件已保留。",
        )
    from app.services.checkpoint_execution import checkpoint_source, interpret_checkpoint
    if source is not None:
        source = checkpoint_source(source, interpret_checkpoint(source, snapshot, graph))
    interrupt = application_planning_interrupt_from_snapshot(snapshot)
    if interrupt is not None and (
        source is None
        or source.status
        not in {DurableExecutionStatus.FAILED, DurableExecutionStatus.INTERRUPTED,
                DurableExecutionStatus.CANCELLED, DurableExecutionStatus.STOPPED}
    ):
        # 父 Graph 已等待确认不代表后台 UI worker 仍存在；只读报告，用户重试才重入节点。
        interrupted_pages = (
            interrupted_ui_design_pages(dict(snapshot.values), workspace)
            if interrupt.get("artifact") == "ui_designs" else []
        )
        return build_application_planning_recovery_projection(
            classification="awaiting_user",
            source=source,
            thread_id=thread_id,
            user_action_required=True,
            input_committed=input_committed,
            reason_code="NATIVE_APPLICATION_PLANNING_INTERRUPT",
            message="当前应用规划正在等待你的确认。",
            ui_generation_recovery=(
                {"pageIds": interrupted_pages} if interrupted_pages else None
            ),
        )
    if source is None:
        return build_application_planning_recovery_projection(
            classification="legacy_unverified",
            source=None,
            thread_id=thread_id,
            reason_code="DURABLE_APPLICATION_PLANNING_EXECUTION_MISSING",
            message="当前规划现场缺少可验证的执行记录，无法安全自动恢复。",
        )

    if source.status is DurableExecutionStatus.RUNNING:
        return build_application_planning_recovery_projection(
            classification="running",
            source=source,
            thread_id=thread_id,
            input_committed=input_committed,
            reason_code="DURABLE_APPLICATION_PLANNING_RUNNING",
            message="当前规划仍在运行。",
        )
    if source.status in {
        DurableExecutionStatus.COMPLETED,
        DurableExecutionStatus.CANCELLED,
        DurableExecutionStatus.STOPPED,
    }:
        classification, message, _ = terminal_application_planning_projection_fields(
            source.status
        )
        return build_application_planning_recovery_projection(
            classification=classification,
            source=source,
            thread_id=thread_id,
            input_committed=input_committed,
            reason_code=f"DURABLE_APPLICATION_PLANNING_{source.status.name}",
            message=message,
        )
    # 没有原生确认现场时不相信旁路索引中的 awaiting_user；沿统一恢复入口处理。
    if source.status is DurableExecutionStatus.AWAITING_USER:
        source = source.model_copy(update={"status": DurableExecutionStatus.INTERRUPTED})

    from app.services.business_node_retry import retry_action_plan
    action_plan = await retry_action_plan(source, graph)

    recovery_action_plan = action_plan.model_dump(mode="json", by_alias=True)
    if action_plan.primary_action is not None:
        return build_application_planning_recovery_projection(
            classification="ready_to_continue",
            source=source,
            thread_id=thread_id,
            can_continue=True,
            input_committed=input_committed,
            reason_code=action_plan.reason_code,
            message=action_plan.message,
            recovery_action_plan=recovery_action_plan,
        )
    return build_application_planning_recovery_projection(
        classification="blocked",
        source=source,
        thread_id=thread_id,
        input_committed=input_committed,
        reason_code=action_plan.reason_code,
        message=action_plan.message,
        recovery_action_plan=recovery_action_plan,
    )
