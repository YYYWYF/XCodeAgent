"""解析页面刷新时应展示的唯一 Planning/Confirmation 状态。"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from app.services.build_context_resolver import resolve_confirmation_context
from app.services.build_task_confirmation import build_task_confirmation_read_model
from app.workspace.task_documents import (
    build_task_plan_lifecycle_lock,
    load_confirmed_build_task_plan,
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
    # 仅记录 Pending 的来源 Workflow Run，不能作为后续 execution 接管令牌。
    workflowRunId: str
    ownerSessionId: str
    draftDigest: str
    buildExecutionScope: dict[str, Any]
    confirmation: dict[str, Any]
    message: str


def _pending_plan(workspace: str) -> tuple[dict[str, Any] | None, Any | None]:
    """读取并校验唯一 PendingPlan 候选，不从其他状态补全其正文或身份。"""

    pending = load_pending_build_task_plan({"workspace": workspace})
    if pending is None:
        return None, None
    return pending, validate_pending_self_digest(pending)


def _pending_confirmation(
    workspace: str,
    pending: dict[str, Any],
    identity: Any,
) -> dict[str, Any]:
    """从当前 Pending 构造与实时 DAG 确认卡相同的安全只读投影。"""

    scope = dict(identity.build_execution_scope)
    read_model = build_task_confirmation_read_model(pending, scope)
    target_type = str(scope.get("type") or "")
    context_error: str | None = None
    if target_type in {"page", "endpoint"}:
        try:
            context = resolve_confirmation_context(workspace, scope)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            # Refresh 只读恢复不能因正式上下文损坏而伪造空接口详情或升级为 500。
            context_error = str(exc).strip() or "正式上下文解析失败。"
        else:
            read_model = build_task_confirmation_read_model(
                pending,
                scope,
                project_plan=context["project_plan"],
                build_context=context["build_context"],
            )

    confirmation: dict[str, Any] = {
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
    }
    if context_error is None or target_type not in {"page", "endpoint"}:
        confirmation["targetReview"] = read_model["targetReview"]
    else:
        confirmation["errors"] = [
            "当前 PendingPlan 仍存在，但无法从最新正式 TechnicalPlan 重建目标详情。",
            context_error,
        ]
        confirmation["message"] = "当前 PendingPlan 仍存在，但无法恢复目标详情。"
    return confirmation


def _identity_matches(value: Any, identity: Any, *, camel_case: bool = False) -> bool:
    """按当前存储对象的字段格式严格比较 PlanningRun 与 draft digest。"""

    planning_run_key = "planningRunId" if camel_case else "planning_run_id"
    draft_digest_key = "draftDigest" if camel_case else "draft_digest"
    return (
        isinstance(value, dict)
        and value.get(planning_run_key) == identity.planning_run_id
        and value.get(draft_digest_key) == identity.draft_digest
    )


def _pending_is_terminal_residue(workspace: str, identity: Any) -> bool:
    """判断当前 Pending 是否已被 Formal 确认或 authoritative Abandon 终结。"""

    try:
        formal = load_confirmed_build_task_plan(workspace)
    except (OSError, TypeError, ValueError):
        # Formal 无法安全读取时不能伪造终态；由当前 Pending 的精确身份继续决定投影。
        formal = None
    if isinstance(formal, dict) and _identity_matches(formal.get("confirmed_from"), identity):
        return True

    try:
        from app.services.application_lifecycle import (
            _application_lifecycle_lock,
            application_lifecycle_path,
            load_application_lifecycle,
        )

        with _application_lifecycle_lock(application_lifecycle_path(workspace)):
            lifecycle = load_application_lifecycle(workspace)
    except (OSError, TypeError, ValueError):
        # 生命周期损坏时没有可验证的 Abandon 身份，不把未知状态误判为终态。
        lifecycle = None
    marker = lifecycle.extensions.get("planningResultLifecycle") if lifecycle else None
    return (
        isinstance(marker, dict)
        and marker.get("schemaVersion") == "planning-result-lifecycle.v1"
        and marker.get("status") == "abandoned"
        and _identity_matches(marker, identity, camel_case=True)
    )


def _resolve_actionable_pending_plan_locked(
    workspace: str,
) -> tuple[dict[str, Any], Any] | None:
    """在已持有 Pending 生命周期锁时解析唯一可行动 PendingPlan。"""

    pending, identity = _pending_plan(workspace)
    if pending is None or _pending_is_terminal_residue(workspace, identity):
        return None
    return pending, identity


def resolve_actionable_pending_plan(
    workspace: str,
) -> tuple[dict[str, Any], Any] | None:
    """返回唯一尚未终结的 PendingPlan；Confirmed/Abandoned residue 返回空。"""

    with build_task_plan_lifecycle_lock(workspace):
        return _resolve_actionable_pending_plan_locked(workspace)


def _resolve_planning_refresh_state_locked(
    workspace: str,
) -> PlanningRefreshState:
    """在已持有 Pending 生命周期锁时构造完整刷新投影。"""

    actionable = _resolve_actionable_pending_plan_locked(workspace)
    if actionable is None:
        return {
            "schemaVersion": "planning-refresh.v1",
            "source": "none",
            "status": "idle",
            "message": "当前没有可恢复的 PendingPlan。",
        }
    pending, pending_identity = actionable

    return {
        "schemaVersion": "planning-refresh.v1",
        "source": "pending_plan",
        "status": "awaiting_confirmation",
        "planningRunId": pending_identity.planning_run_id,
        "workflowRunId": pending_identity.workflow_run_id,
        "ownerSessionId": pending_identity.owner_session_id,
        "draftDigest": pending_identity.draft_digest,
        "buildExecutionScope": dict(pending_identity.build_execution_scope),
        "confirmation": _pending_confirmation(workspace, pending, pending_identity),
        "message": "已从 PendingPlan 恢复待确认任务规划。",
    }


def resolve_planning_refresh_state(
    workspace: str,
) -> PlanningRefreshState:
    """只按当前 PendingPlan 是否仍可行动返回 awaiting_confirmation 或 idle。

    旧的 awaiting_user、activeRunId、resourceLocks 或其他运行时残留不参与判断；
    Confirm/Abandon 的清理残留由其精确终态身份压制。
    """

    with build_task_plan_lifecycle_lock(workspace):
        return _resolve_planning_refresh_state_locked(workspace)
