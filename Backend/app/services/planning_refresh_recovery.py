"""解析页面刷新时应展示的唯一 Planning/Confirmation 状态。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, TypedDict

from app.services.build_task_confirmation import build_task_confirmation_read_model
from app.workspace.task_documents import (
    load_pending_build_task_plan,
    validate_pending_self_digest,
)


PlanningRefreshSource = Literal["pending_plan", "none"]
PlanningRefreshStatus = Literal["awaiting_confirmation", "idle"]


class PlanningRefreshState(TypedDict, total=False):
    """定义 lifecycle 读取 API 返回给前端的紧凑恢复状态。"""

    schemaVersion: Literal["planning-refresh.v1"]
    source: PlanningRefreshSource
    status: PlanningRefreshStatus
    planningRunId: str
    workflowRunId: str
    ownerSessionId: str
    draftDigest: str
    buildExecutionScope: dict[str, Any]
    confirmation: dict[str, Any]
    message: str


def _pending_plan(workspace: str) -> tuple[dict[str, Any] | None, Any | None]:
    """只读取并校验唯一 PendingPlan，不从其他状态补全或推断。"""

    pending = load_pending_build_task_plan({"workspace": workspace})
    if pending is None:
        return None, None
    return pending, validate_pending_self_digest(pending)


def _pending_confirmation(pending: dict[str, Any], identity: Any) -> dict[str, Any]:
    """从当前 Pending 构造与实时 DAG 确认卡相同的安全只读投影。"""

    scope = dict(identity.build_execution_scope)
    read_model = build_task_confirmation_read_model(pending, scope)
    return {
        "mode": "build_task_plan_confirmation",
        "status": "requires_user_input",
        "message": "Build DAG 已生成，请确认任务规划后再进入 Build。",
        "actionValues": ["confirm", "abandon", "regenerate"],
        "confirmationStatus": "pending",
        "buildExecutionScope": scope,
        "draftIdentity": {
            "ownerSessionId": identity.owner_session_id,
            "planningRunId": identity.planning_run_id,
            "draftDigest": identity.draft_digest,
        },
        "taskPlan": {
            "version": pending.get("version"),
            "schemaVersion": pending.get("schema_version"),
            "status": pending.get("status"),
            "confirmationStatus": "pending",
            "summary": pending.get("summary") or {},
            "scopeTasks": read_model["scopeTasks"],
            "reusedPrerequisites": read_model["reusedPrerequisites"],
            "retainedTaskSummary": read_model["retainedTaskSummary"],
        },
        "targetReview": read_model["targetReview"],
    }


def resolve_planning_refresh_state(
    workspace: str,
    *,
    lifecycle: Any | None = None,
    runtime_active: Callable[[str], bool] | None = None,
) -> PlanningRefreshState:
    """只按 PendingPlan 是否存在返回 awaiting_confirmation 或 idle。

    旧的 awaiting_user、activeRunId、resourceLocks 或其他运行时残留不参与判断；
    只有 Confirm/Abandon 真正移除 PendingPlan 后，投影才会变为 idle。保留的两个
    旧调用参数仅用于平滑切换读取调用方，函数不会读取它们。
    """

    del lifecycle, runtime_active
    pending, pending_identity = _pending_plan(workspace)
    if pending is None:
        return {
            "schemaVersion": "planning-refresh.v1",
            "source": "none",
            "status": "idle",
            "message": "当前没有可恢复的 PendingPlan。",
        }

    return {
        "schemaVersion": "planning-refresh.v1",
        "source": "pending_plan",
        "status": "awaiting_confirmation",
        "planningRunId": pending_identity.planning_run_id,
        "workflowRunId": pending_identity.workflow_run_id,
        "ownerSessionId": pending_identity.owner_session_id,
        "draftDigest": pending_identity.draft_digest,
        "buildExecutionScope": dict(pending_identity.build_execution_scope),
        "confirmation": _pending_confirmation(pending, pending_identity),
        "message": "已从 PendingPlan 恢复待确认任务规划。",
    }
