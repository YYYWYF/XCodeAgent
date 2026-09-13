"""统一判断 Durable Execution 是否可以进入 Recovery Safety Assessment。"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionFailureOrigin,
)


@dataclass(frozen=True)
class RecoverySourceAdmission:
    """保存 source admission 的 fail-closed 判断和稳定原因码。"""

    admissible: bool
    reason_code: str


def assess_recovery_source(
    source: DurableExecutionRecord,
) -> RecoverySourceAdmission:
    """只依据 durable status 与结构化 failure evidence 判断 source 资格。"""

    if source.status is DurableExecutionStatus.INTERRUPTED:
        return RecoverySourceAdmission(True, "SOURCE_INTERRUPTED_ADMISSIBLE")
    if source.status is not DurableExecutionStatus.FAILED:
        return RecoverySourceAdmission(False, "SOURCE_STATUS_NOT_RECOVERY_ADMISSIBLE")
    failure = source.failure
    if failure is None:
        return RecoverySourceAdmission(False, "FAILURE_EVIDENCE_MISSING")
    if failure.origin not in {
        ExecutionFailureOrigin.MODEL_CALL,
        ExecutionFailureOrigin.EXTERNAL_DEPENDENCY,
    }:
        return RecoverySourceAdmission(False, "FAILURE_ORIGIN_NOT_ADMISSIBLE")
    if not failure.replay_compatible:
        return RecoverySourceAdmission(False, "FAILURE_NOT_REPLAY_COMPATIBLE")
    return RecoverySourceAdmission(True, "FAILED_REPLAY_COMPATIBLE")


__all__ = ["RecoverySourceAdmission", "assess_recovery_source"]
