"""Application Planning 已提交需求澄清回答的专用恢复策略。"""

from __future__ import annotations

from typing import Any

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

        values = getattr(snapshot, "values", {})
        values = values if isinstance(values, dict) else {}
        if (
            source.execution_kind != "application_planning"
            or source.status is not DurableExecutionStatus.INTERRUPTED
            or point.next_nodes != ["requirements"]
            or application_planning_interrupt_from_snapshot(snapshot) is not None
            or str(values.get("active_run_id") or "").strip() != source.run_id
            or not application_planning_committed_input(snapshot)
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
]
