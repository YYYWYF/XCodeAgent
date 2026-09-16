"""P0.3B execution lineage head resolution and crash reconciliation。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryAttempt,
    RecoveryAttemptStatus,
    RecoveryExecutionError,
    RecoveryLifecycleOwnershipMode,
)
from app.persistence.execution_recovery import (
    fail_recovery_attempt_prestart,
    get_recovery_attempt,
    list_executions_for_thread,
    list_recovery_attempts_for_thread,
    update_recovery_attempt,
)
from app.services.application_lifecycle import (
    claim_application_planning_run_for_recovery,
    load_application_lifecycle,
)


class RecoveryLineageState(StrEnum):
    """定义当前 lineage head 对产品恢复能力的稳定状态。"""

    NO_HEAD = "NO_HEAD"
    RECOVERABLE_HEAD = "RECOVERABLE_HEAD"
    RUNNING_HEAD = "RUNNING_HEAD"
    AWAITING_USER_HEAD = "AWAITING_USER_HEAD"
    COMPLETED = "COMPLETED"
    RECOVERY_IN_FLIGHT = "RECOVERY_IN_FLIGHT"
    AMBIGUOUS = "AMBIGUOUS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class RecoveryLineageResolution:
    """保存由 durable execution 与 recovery edges 解析出的唯一 lineage head。"""

    head: DurableExecutionRecord | None
    state: RecoveryLineageState
    reason_code: str


_LINEAGE_EDGE_STATUSES = frozenset(
    {
        RecoveryAttemptStatus.PREPARING,
        RecoveryAttemptStatus.HANDED_OFF,
        RecoveryAttemptStatus.FINALIZING,
        RecoveryAttemptStatus.STARTED,
        RecoveryAttemptStatus.FINALIZATION_FAILED,
    }
)
_IN_FLIGHT_ATTEMPT_STATUSES = frozenset(
    {
        RecoveryAttemptStatus.PREPARING,
        RecoveryAttemptStatus.HANDED_OFF,
        RecoveryAttemptStatus.FINALIZING,
    }
)
_LINEAGE_EXECUTION_STATUSES = frozenset(
    {
        DurableExecutionStatus.RUNNING,
        DurableExecutionStatus.AWAITING_USER,
        DurableExecutionStatus.FAILED,
        DurableExecutionStatus.INTERRUPTED,
    }
)


async def resolve_recovery_lineage_head(
    workspace: str,
    *,
    thread_id: str,
    execution_kind: str | None = None,
    max_hops: int = 32,
) -> RecoveryLineageResolution:
    """按 thread 的完整 durable facts 解析唯一当前 recovery source。"""

    executions = await list_executions_for_thread(
        workspace,
        thread_id=thread_id,
    )
    attempts = await list_recovery_attempts_for_thread(
        workspace,
        thread_id=thread_id,
    )
    records = {record.run_id: record for record in executions}
    recovery_edges: dict[str, list[RecoveryAttempt]] = {}
    incoming_edges: dict[str, list[RecoveryAttempt]] = {}
    ignored_prestart_children: set[str] = set()
    malformed = False

    for attempt in attempts:
        source = records.get(attempt.source_run_id)
        child = records.get(attempt.new_run_id)
        if source is None or child is None:
            # Attempt facts引用了当前查询范围外的 execution，不能用猜测恢复。
            malformed = True
            continue
        if execution_kind is not None and (
            source.execution_kind != execution_kind
            or child.execution_kind != execution_kind
        ):
            # 同一 thread 的其他 execution kind 不属于本次 lineage 查询范围。
            continue
        if (
            attempt.thread_id != thread_id
            or source.thread_id != thread_id
            or child.thread_id != thread_id
            or _workspace_identity(source.workspace) != _workspace_identity(workspace)
            or _workspace_identity(child.workspace) != _workspace_identity(workspace)
        ):
            malformed = True
            continue
        if attempt.status is RecoveryAttemptStatus.FAILED_PRESTART:
            ignored_prestart_children.add(attempt.new_run_id)
            continue
        if attempt.status not in _LINEAGE_EDGE_STATUSES:
            malformed = True
            continue
        recovery_edges.setdefault(attempt.source_run_id, []).append(attempt)
        incoming_edges.setdefault(attempt.new_run_id, []).append(attempt)

    if malformed:
        return RecoveryLineageResolution(
            head=None,
            state=RecoveryLineageState.AMBIGUOUS,
            reason_code="RECOVERY_LINEAGE_AMBIGUOUS",
        )

    participants = {
        record.run_id
        for record in executions
        if (
            (execution_kind is None or record.execution_kind == execution_kind)
            and record.status in _LINEAGE_EXECUTION_STATUSES
        )
        and record.run_id not in ignored_prestart_children
    }
    for source_run_id, children in recovery_edges.items():
        participants.add(source_run_id)
        participants.update(attempt.new_run_id for attempt in children)
    if not participants:
        return RecoveryLineageResolution(
            head=None,
            state=RecoveryLineageState.NO_HEAD,
            reason_code="RECOVERY_LINEAGE_NO_HEAD",
        )

    if any(len(children) != 1 for children in recovery_edges.values()) or any(
        len(parents) != 1 for parents in incoming_edges.values()
    ):
        return RecoveryLineageResolution(
            head=None,
            state=RecoveryLineageState.AMBIGUOUS,
            reason_code="RECOVERY_LINEAGE_AMBIGUOUS",
        )

    for start_run_id in participants:
        current = start_run_id
        visited: set[str] = set()
        for _ in range(max_hops):
            if current in visited:
                return RecoveryLineageResolution(
                    head=None,
                    state=RecoveryLineageState.AMBIGUOUS,
                    reason_code="RECOVERY_LINEAGE_AMBIGUOUS",
                )
            visited.add(current)
            children = recovery_edges.get(current, ())
            if not children:
                break
            current = children[0].new_run_id
        else:
            return RecoveryLineageResolution(
                head=None,
                state=RecoveryLineageState.AMBIGUOUS,
                reason_code="RECOVERY_LINEAGE_AMBIGUOUS",
            )

    leaves = [
        run_id
        for run_id in participants
        if not any(
            attempt.new_run_id in participants
            for attempt in recovery_edges.get(run_id, ())
        )
    ]
    if len(leaves) != 1:
        return RecoveryLineageResolution(
            head=None,
            state=RecoveryLineageState.AMBIGUOUS,
            reason_code="RECOVERY_LINEAGE_AMBIGUOUS",
        )

    head = records.get(leaves[0])
    if head is None:
        return RecoveryLineageResolution(
            head=None,
            state=RecoveryLineageState.AMBIGUOUS,
            reason_code="RECOVERY_LINEAGE_AMBIGUOUS",
        )
    head_attempts = incoming_edges.get(head.run_id, ())
    if len(head_attempts) == 1 and head_attempts[0].status in _IN_FLIGHT_ATTEMPT_STATUSES:
        state = RecoveryLineageState.RECOVERY_IN_FLIGHT
        reason_code = "RECOVERY_IN_FLIGHT"
    elif head.status is DurableExecutionStatus.RUNNING:
        state = RecoveryLineageState.RUNNING_HEAD
        reason_code = "RECOVERY_LINEAGE_HEAD_RUNNING"
    elif head.status is DurableExecutionStatus.AWAITING_USER:
        state = RecoveryLineageState.AWAITING_USER_HEAD
        reason_code = "RECOVERY_LINEAGE_HEAD_AWAITING_USER"
    elif head.status is DurableExecutionStatus.COMPLETED:
        state = RecoveryLineageState.COMPLETED
        reason_code = "RECOVERY_LINEAGE_COMPLETED"
    elif head.status in {
        DurableExecutionStatus.FAILED,
        DurableExecutionStatus.INTERRUPTED,
    }:
        state = RecoveryLineageState.RECOVERABLE_HEAD
        reason_code = "RECOVERY_LINEAGE_HEAD_RESOLVED"
    else:
        state = RecoveryLineageState.BLOCKED
        reason_code = "RECOVERY_LINEAGE_BLOCKED"
    return RecoveryLineageResolution(
        head=head,
        state=state,
        reason_code=reason_code,
    )


def _workspace_identity(value: str) -> str:
    """把 durable workspace 路径规范化为可比较的身份键。"""

    return str(Path(value).expanduser().resolve(strict=False))


async def reconcile_recovery_attempt(
    *,
    workspace: str,
    new_run_id: str,
    graph: Any | None = None,
) -> Any:
    """收敛崩溃在 PREPARING/HANDED_OFF/FINALIZING 阶段留下的 child lineage。"""

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
        elif attempt.lifecycle_ownership_mode is RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP:
            if lifecycle is None or attempt.source_lifecycle_revision is None:
                attempt = await fail_recovery_attempt_prestart(
                    workspace=workspace,
                    new_run_id=new_run_id,
                    failure_code="RECOVERY_STATE_DRIFT",
                )
            else:
                try:
                    await claim_application_planning_run_for_recovery(
                        workspace,
                        new_run_id=attempt.new_run_id,
                        thread_id=attempt.thread_id,
                        expected_lifecycle_revision=attempt.source_lifecycle_revision,
                    )
                except Exception:
                    attempt = await fail_recovery_attempt_prestart(
                        workspace=workspace,
                        new_run_id=new_run_id,
                        failure_code="RECOVERY_STATE_DRIFT",
                    )
                else:
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
    if attempt is not None and attempt.status in {
        RecoveryAttemptStatus.HANDED_OFF,
        RecoveryAttemptStatus.FINALIZING,
    }:
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


__all__ = [
    "RecoveryLineageResolution",
    "RecoveryLineageState",
    "reconcile_recovery_attempt",
    "resolve_recovery_lineage_head",
]
