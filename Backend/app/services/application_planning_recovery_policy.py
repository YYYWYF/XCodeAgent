"""Application Planning 已提交需求澄清回答的专用恢复策略。"""

from __future__ import annotations

from typing import Any

from app.domain.application_lifecycle import (
    ApplicationLifecycle,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryDecision,
    RecoveryPoint,
    RecoveryStrategy,
)
from app.protocols.application_planning_interrupt import (
    application_planning_interrupt_from_snapshot,
)
from app.services.execution_recovery_strategy import RecoveryStrategyAssessment


def application_planning_committed_input(snapshot: Any) -> bool:
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


def application_planning_committed_input_recovery_candidate(
    *,
    source: DurableExecutionRecord,
    point: RecoveryPoint,
    snapshot: Any,
) -> bool:
    """判断 checkpoint 是否属于已提交 Requirement 回答的专用恢复候选现场。"""

    values = getattr(snapshot, "values", {})
    values = values if isinstance(values, dict) else {}
    return (
        source.execution_kind == "application_planning"
        and source.status is DurableExecutionStatus.INTERRUPTED
        and point.next_nodes == ["requirements"]
        and application_planning_interrupt_from_snapshot(snapshot) is None
        and str(values.get("active_run_id") or "").strip() == source.run_id
        and application_planning_committed_input(snapshot)
    )


def application_planning_committed_input_lifecycle_compatible(
    *,
    source: DurableExecutionRecord,
    point: RecoveryPoint,
    snapshot: Any,
    lifecycle: ApplicationLifecycle,
) -> bool:
    """仅认可已提交澄清回答进入 requirements 时的两个生命周期窗口。"""

    if not application_planning_committed_input_recovery_candidate(
        source=source,
        point=point,
        snapshot=snapshot,
    ):
        return False
    if (
        lifecycle.initialization.thread_id != source.thread_id
        or lifecycle.active_run_id != source.run_id
    ):
        return False

    expected_revision = point.lifecycle_revision
    if (
        lifecycle.initialization.stage
        is ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION
        and lifecycle.initialization.status
        is ApplicationLifecycleStatus.AWAITING_USER
    ):
        return expected_revision is None or lifecycle.revision == expected_revision
    if (
        lifecycle.initialization.stage
        is ApplicationLifecycleStage.ANALYZING_REQUIREMENT
        and lifecycle.initialization.status is ApplicationLifecycleStatus.RUNNING
    ):
        return expected_revision is None or lifecycle.revision in {
            expected_revision,
            expected_revision + 1,
        }
    return False


class ApplicationPlanningCommittedInputReplayPolicy:
    """只放行已提交 Requirement 澄清回答后的 requirements checkpoint。"""

    def assess(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
    ) -> RecoveryStrategyAssessment | None:
        """同时校验执行、checkpoint、交互身份和 Native Interrupt。"""

        if not application_planning_committed_input_recovery_candidate(
            source=source,
            point=point,
            snapshot=snapshot,
        ):
            return None
        return RecoveryStrategyAssessment(
            decision=RecoveryDecision.READY_NATIVE,
            strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
            reason_code="APPLICATION_PLANNING_INPUT_COMMITTED_REPLAY_SAFE",
            reason="已提交的需求澄清回答和 requirements checkpoint 已通过专用重放策略校验。",
        )


__all__ = [
    "ApplicationPlanningCommittedInputReplayPolicy",
    "application_planning_committed_input",
    "application_planning_committed_input_recovery_candidate",
    "application_planning_committed_input_lifecycle_compatible",
]
