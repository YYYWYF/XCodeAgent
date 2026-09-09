"""在下一次 Reconcile 前恢复或阻断未完成的本地事务。"""

from __future__ import annotations

from pathlib import Path

from app.services.template_reconcile.applier import rollback_workspace
from app.services.template_reconcile.runtime_state import (
    ReconcileAttempt,
    load_reconcile_attempt,
    save_reconcile_attempt,
)


class TemplateReconcileRecoveryError(ValueError):
    """表示未完成事务不能安全自动恢复，需要人工处理。"""

    code = "RECONCILE_RECOVERY_REQUIRED"


def recover_reconcile_attempt(workspace: str | Path, *, change_id: str) -> ReconcileAttempt | None:
    """恢复失败前的 Apply，或拒绝 metadata 临界区与不匹配的 Attempt。"""

    attempt = load_reconcile_attempt(workspace)
    if attempt is None or attempt.phase == "RECONCILED":
        return None
    if attempt.change_id != change_id:
        raise TemplateReconcileRecoveryError("未完成的 Template Reconcile 与当前 changeId 不一致。")
    if attempt.phase == "COMMITTING_METADATA":
        raise TemplateReconcileRecoveryError("TemplateState 元数据提交可能只完成一半，需要人工恢复。")
    if attempt.phase in {"APPLYING", "VALIDATING"}:
        rollback_workspace(workspace, attempt)
        cleaned = ReconcileAttempt(
            change_id=attempt.change_id,
            technical_plan_sha256=attempt.technical_plan_sha256,
            pre_reconcile_head=attempt.pre_reconcile_head,
            phase="FAILED_CLEAN",
            added_paths=(),
            error="上次 Reconcile 中断，已回退到 preReconcileHead。",
        )
        save_reconcile_attempt(workspace, cleaned)
        return cleaned
    if attempt.phase == "FAILED_CLEAN":
        return attempt
    raise TemplateReconcileRecoveryError("Template Reconcile Runtime State 处于未知状态。")
