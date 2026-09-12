"""P0.3B execution lineage head resolution and crash reconciliation。"""

from __future__ import annotations

from typing import Any

from app.domain.execution_recovery import (
    RecoveryAttempt,
    RecoveryAttemptStatus,
    RecoveryExecutionError,
)
from app.persistence.execution_recovery import (
    fail_recovery_attempt_prestart,
    get_recovery_attempt,
    list_recovery_attempts_from_source,
    update_recovery_attempt,
)
from app.services.application_lifecycle import load_application_lifecycle


async def resolve_recovery_head(
    workspace: str,
    source_run_id: str,
    *,
    max_hops: int = 32,
) -> str:
    """沿 STARTED/HANDED_OFF lineage 找到 source 当前可继续的 canonical head。"""

    current = source_run_id
    visited: set[str] = set()
    for _ in range(max_hops):
        if current in visited:
            raise RecoveryExecutionError(
                "RECOVERY_LINEAGE_CORRUPTED",
                "RecoveryAttempt lineage 检测到循环。",
            )
        visited.add(current)
        attempts = [
            attempt
            for attempt in await list_recovery_attempts_from_source(workspace, current)
            if attempt.status is not RecoveryAttemptStatus.FAILED_PRESTART
        ]
        if not attempts:
            return current
        if len(attempts) != 1:
            raise RecoveryExecutionError(
                "RECOVERY_LINEAGE_CORRUPTED",
                "RecoveryAttempt lineage 存在多个 active child。",
            )
        current = attempts[0].new_run_id
    raise RecoveryExecutionError(
        "RECOVERY_LINEAGE_CORRUPTED",
        f"RecoveryAttempt lineage 超过 {max_hops} 跳。",
    )


async def reconcile_recovery_attempt(
    *,
    workspace: str,
    new_run_id: str,
    graph: Any | None = None,
) -> Any:
    """收敛崩溃在 PREPARING/HANDED_OFF 阶段留下的 child lineage。"""

    attempt = await get_recovery_attempt(workspace, new_run_id)
    if attempt is None:
        return None
    if attempt.status is RecoveryAttemptStatus.PREPARING:
        lifecycle = load_application_lifecycle(workspace)
        source_owned, child_owned = _lifecycle_ownership(
            lifecycle,
            source_run_id=attempt.source_run_id,
            new_run_id=attempt.new_run_id,
        )
        if child_owned:
            attempt = await update_recovery_attempt(
                workspace=workspace,
                new_run_id=new_run_id,
                status=RecoveryAttemptStatus.HANDED_OFF,
            )
        elif source_owned or lifecycle is None:
            attempt = await fail_recovery_attempt_prestart(
                workspace=workspace,
                new_run_id=new_run_id,
                failure_code="RECOVERY_PRESTART_CRASH",
            )
        else:
            raise RecoveryExecutionError(
                "RECOVERY_LINEAGE_CORRUPTED",
                "PREPARING recovery 的 lifecycle ownership 无法判定。",
            )
    if attempt is not None and attempt.status is RecoveryAttemptStatus.HANDED_OFF:
        if graph is None:
            return attempt
        from app.services.execution_recovery_executor import (
            finalize_handed_off_recovery_attempt,
        )

        return await finalize_handed_off_recovery_attempt(
            workspace=workspace,
            new_run_id=new_run_id,
            graph=graph,
        )
    return attempt


def _lifecycle_ownership(
    lifecycle: Any,
    *,
    source_run_id: str,
    new_run_id: str,
) -> tuple[bool, bool]:
    """判断 lifecycle 当前仍指向 source、已切换 child，或两者都不是。"""

    if lifecycle is None:
        return False, False
    active_executions = getattr(lifecycle, "active_executions", {})
    if isinstance(active_executions, dict):
        if source_run_id in active_executions or new_run_id in active_executions:
            return source_run_id in active_executions, new_run_id in active_executions
    active_run_id = str(getattr(lifecycle, "active_run_id", "") or "")
    return active_run_id == source_run_id, active_run_id == new_run_id


__all__ = ["reconcile_recovery_attempt", "resolve_recovery_head"]
