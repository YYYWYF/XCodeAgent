"""任务确认入口只接受当前磁盘上真实可确认的草稿。"""

from typing import Any

from app.domain.execution_recovery import RecoveryExecutionError
from app.workspace.task_documents import (
    load_pending_build_task_plan,
    validate_pending_planning_provenance,
    validate_pending_self_digest,
)


def require_pending_task_plan(state: dict[str, Any]) -> dict[str, Any]:
    """缺少有效草稿时显式失败，避免虚假等待确认继续占用执行资源。"""

    try:
        pending = load_pending_build_task_plan(state)
        if pending is None:
            raise ValueError("待确认草稿不存在。")
        validate_pending_self_digest(pending)
        validate_pending_planning_provenance(pending, pending.get("planning_provenance"))
    except (OSError, ValueError, TypeError) as exc:
        # checkpoint 续跑会由统一重试协调器自动降级；普通执行沿现有失败清理收口。
        raise RecoveryExecutionError(
            "TASK_PLAN_PENDING_UNAVAILABLE",
            "待确认任务计划不存在或已失效，请重试重新生成任务计划。",
        ) from exc
    return pending
