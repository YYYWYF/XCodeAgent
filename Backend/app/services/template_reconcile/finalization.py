"""Template Reconcile 进入前的最小 lifecycle CAS 防重入能力。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.domain.application_revision import ActiveFormalRevision
from app.domain.application_lifecycle import utc_now
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    load_application_lifecycle,
    write_application_lifecycle,
)


@dataclass(frozen=True)
class ReconcileFinalizationClaim:
    """表示本次调用是否刚取得 Reconcile 进入权。"""

    acquired: bool
    active_revision: ActiveFormalRevision


def claim_template_reconcile_finalization(
    workspace: str | Path,
    *,
    change_id: str,
) -> ReconcileFinalizationClaim:
    """以 lifecycle revision CAS 将当前 TechnicalPlan Revision 标记为 Reconcile 中。"""

    current = _required_lifecycle(workspace)
    active = current.active_formal_revision
    if active is None or active.change_id != change_id:
        raise ApplicationLifecycleConflictError("没有匹配的 active formal revision。")
    if active.status == "template_reconciling":
        return ReconcileFinalizationClaim(acquired=False, active_revision=active)
    if active.continuation_token_sha256 is not None or active.continuation_consumed_at is not None:
        raise ApplicationLifecycleConflictError("当前 formal revision 已进入 continuation，不能再进入 Template Reconcile。")
    if active.status not in {"drafting", "awaiting_user", "template_reconcile_failed"}:
        raise ApplicationLifecycleConflictError(
            f"当前 formal revision 状态 {active.status} 不允许进入 Template Reconcile。"
        )
    next_active = active.model_copy(
        update={
            "status": "template_reconciling",
            "current_artifact": "technical-plan",
        }
    )
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": current.revision + 1,
            "active_formal_revision": next_active,
        }
    )
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)
    return ReconcileFinalizationClaim(acquired=True, active_revision=next_active)


def mark_template_reconcile_failed(
    workspace: str | Path,
    *,
    change_id: str,
) -> ActiveFormalRevision:
    """把已经取得进入权但未成功的 Reconcile 标记为可展示失败。"""

    current = _required_lifecycle(workspace)
    active = current.active_formal_revision
    if active is None or active.change_id != change_id:
        raise ApplicationLifecycleConflictError("没有匹配的 active formal revision。")
    if active.status != "template_reconciling":
        raise ApplicationLifecycleConflictError("只有 template_reconciling 状态可以标记 Reconcile 失败。")
    next_active = active.model_copy(update={"status": "template_reconcile_failed"})
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": current.revision + 1,
            "active_formal_revision": next_active,
        }
    )
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)
    return next_active


def _required_lifecycle(workspace: str | Path):
    """读取已有 lifecycle，并拒绝在未初始化 Workspace 上声明 Reconcile。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        raise ApplicationLifecycleConflictError("application lifecycle 尚未初始化。")
    return current
