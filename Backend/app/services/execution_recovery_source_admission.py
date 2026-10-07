"""统一判断 Durable Execution 是否可以进入 Recovery Safety Assessment。"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
)


@dataclass(frozen=True)
class RecoverySourceAdmission:
    """保存 source admission 的 fail-closed 判断和稳定原因码。"""

    admissible: bool
    reason_code: str


def assess_recovery_source(
    source: DurableExecutionRecord,
) -> RecoverySourceAdmission:
    """区分可恢复异常终止与缺少异常证据的业务 FAILED 终态。"""

    if source.status is DurableExecutionStatus.INTERRUPTED:
        return RecoverySourceAdmission(True, "SOURCE_INTERRUPTED_ADMISSIBLE")
    if source.status is DurableExecutionStatus.FAILED:
        if source.failure is None:
            return RecoverySourceAdmission(False, "FAILED_EXCEPTION_EVIDENCE_MISSING")
        return RecoverySourceAdmission(True, "SOURCE_FAILED_EXCEPTION_ADMISSIBLE")
    return RecoverySourceAdmission(False, "SOURCE_STATUS_NOT_RECOVERY_ADMISSIBLE")


__all__ = ["RecoverySourceAdmission", "assess_recovery_source"]
