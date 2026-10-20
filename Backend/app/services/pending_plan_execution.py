"""按当前待确认草稿身份定位需要随放弃操作收口的执行。"""

from typing import Any

from app.domain.application_lifecycle import ApplicationLifecycle, PendingInteractionType, WorkbenchExecutionStatus


def pending_plan_execution_ids(
    lifecycle: ApplicationLifecycle, *, owner_session_id: str,
    planning_run_id: str, draft_digest: str, build_execution_scope: dict[str, Any],
) -> tuple[str, ...]:
    """只选择同 owner、范围及 DraftIdentity 的未提交确认门，不使用生成来源 Run 猜测。"""
    matched = []
    for run_id, execution in lifecycle.active_executions.items():
        pending = execution.pending_interaction
        if (
            execution.phase != "prepare_build_tasks"
            or execution.status != WorkbenchExecutionStatus.AWAITING_USER
            or execution.owner_session_id != owner_session_id
            or execution.scope != build_execution_scope.get("type")
            or execution.target_id != build_execution_scope.get("targetId")
            or pending is None or pending.submitted_at is not None
            or pending.type != PendingInteractionType.TASK_PLAN_CONFIRMATION
        ):
            continue
        identity = pending.payload.get("draftIdentity")
        if not isinstance(identity, dict) or (
            identity.get("ownerSessionId") != owner_session_id
            or identity.get("planningRunId") != planning_run_id
            or identity.get("draftDigest") != draft_digest
        ):
            continue
        matched.append(run_id)
    return tuple(matched)
