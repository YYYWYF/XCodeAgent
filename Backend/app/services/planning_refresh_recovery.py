"""解析页面刷新时应展示的唯一 Planning/Confirmation 状态。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, TypedDict

from app.domain.application_lifecycle import ApplicationLifecycle, PendingInteractionType
from app.services.build_task_confirmation import build_task_confirmation_read_model
from app.services.planning_run_contracts import PlanningRunProjection
from app.services.planning_run_progress import project_planning_run_progress
from app.workspace.planning_run_documents import load_planning_run
from app.workspace.task_documents import (
    build_task_plan_json_path,
    build_task_plan_pending_json_path,
    build_task_plan_sha256,
    load_confirmed_build_task_plan,
    load_pending_build_task_plan,
    validate_pending_self_digest,
)


PlanningRefreshSource = Literal[
    "pending",
    "abandoned",
    "active_planning_run",
    "confirmed_plan",
    "none",
]
PlanningRefreshStatus = Literal[
    "awaiting_confirmation",
    "abandoned",
    "planning",
    "planning_run_interrupted",
    "confirmed",
    "idle",
]


class PlanningRefreshState(TypedDict, total=False):
    """定义 lifecycle 读取 API 返回给前端的紧凑恢复状态。"""

    schemaVersion: Literal["planning-refresh.v1"]
    source: PlanningRefreshSource
    status: PlanningRefreshStatus
    planningRunId: str
    workflowRunId: str
    threadId: str
    ownerSessionId: str
    draftDigest: str
    buildExecutionScope: dict[str, Any]
    dagGeneration: dict[str, Any]
    confirmation: dict[str, Any]
    confirmedPlanDigest: str
    message: str


RuntimeActiveReader = Callable[[str], bool]


def _abandoned_planning_result(
    lifecycle: ApplicationLifecycle | None,
) -> dict[str, Any] | None:
    """严格读取 application lifecycle 中持久化的当前 Abandon tombstone。"""

    value = lifecycle.extensions.get("planningResultLifecycle") if lifecycle is not None else None
    if not isinstance(value, dict):
        return None
    planning_run_id = value.get("planningRunId")
    draft_digest = value.get("draftDigest")
    base_digest = value.get("baseConfirmedPlanDigest")
    scope = value.get("buildExecutionScope")
    if (
        value.get("schemaVersion") != "planning-result-lifecycle.v1"
        or value.get("status") != "abandoned"
        or not isinstance(planning_run_id, str)
        or not planning_run_id.strip()
        or not isinstance(draft_digest, str)
        or len(draft_digest) != 64
        or any(character not in "0123456789abcdef" for character in draft_digest)
        or (
            base_digest is not None
            and (
                not isinstance(base_digest, str)
                or len(base_digest) != 64
                or any(character not in "0123456789abcdef" for character in base_digest)
            )
        )
        or not isinstance(scope, dict)
    ):
        return None
    return value


def _confirmed_plan(workspace: str) -> tuple[dict[str, Any] | None, bool]:
    """读取当前 Formal，并区分不存在与存在但不是合法 ConfirmedPlan。"""

    path = build_task_plan_json_path({"workspace": workspace})
    try:
        confirmed = load_confirmed_build_task_plan(workspace)
    except (OSError, ValueError, TypeError):
        return None, True
    return confirmed, confirmed is None and (path.exists() or path.is_symlink())


def _pending_plan(workspace: str, *, formal_exists: bool) -> tuple[dict[str, Any] | None, Any | None]:
    """读取并校验 Pending；Formal 已存在时允许忽略无法证明有效的清理残留。"""

    state = {"workspace": workspace}
    try:
        pending = load_pending_build_task_plan(state)
        if pending is None:
            return None, None
        return pending, validate_pending_self_digest(pending)
    except (OSError, ValueError, TypeError):
        if formal_exists and build_task_plan_pending_json_path(state).exists():
            return None, None
        raise


def _planning_run(workspace: str, *, lower_priority_formal_exists: bool) -> PlanningRunProjection | None:
    """严格读取 PlanningRun；合法 Formal 存在时忽略无法解析的低优先级残留。"""

    try:
        payload = load_planning_run({"workspace": workspace})
    except (OSError, ValueError, TypeError):
        if lower_priority_formal_exists:
            return None
        raise
    return PlanningRunProjection.model_validate(payload) if payload is not None else None


def _matching_pending_execution(
    lifecycle: ApplicationLifecycle | None,
    planning_run: PlanningRunProjection | None,
) -> tuple[str, str]:
    """从 lifecycle 找到当前 DAG 确认 execution，优先匹配 PlanningRun 的 Workflow 身份。"""

    if lifecycle is None:
        return (
            planning_run.workflow_run_id if planning_run is not None else "",
            planning_run.thread_id if planning_run is not None else "",
        )
    if planning_run is not None:
        exact = lifecycle.active_executions.get(planning_run.workflow_run_id)
        if exact is not None:
            return exact.run_id, exact.thread_id
    for execution in lifecycle.active_executions.values():
        pending = execution.pending_interaction
        if (
            execution.status == "awaiting_user"
            and pending is not None
            and (
                pending.type == PendingInteractionType.TASK_PLAN_CONFIRMATION
                or pending.payload.get("mode") == "build_task_plan_confirmation"
            )
        ):
            return execution.run_id, execution.thread_id
    return "", ""


def _pending_was_promoted(pending_identity: Any, confirmed: dict[str, Any] | None) -> bool:
    """判断 Pending 是否只是 Formal 提升成功后的精确清理残留。"""

    if confirmed is None or pending_identity is None:
        return False
    promoted_from = confirmed.get("confirmed_from")
    return (
        isinstance(promoted_from, dict)
        and promoted_from.get("planning_run_id") == pending_identity.planning_run_id
        and promoted_from.get("draft_digest") == pending_identity.draft_digest
    )


def _pending_was_abandoned(
    pending_identity: Any,
    abandoned: dict[str, Any] | None,
) -> bool:
    """判断 Pending 是否只是 authoritative Abandon 提交后的精确清理残留。"""

    if abandoned is None or pending_identity is None:
        return False
    return (
        abandoned.get("planningRunId") == pending_identity.planning_run_id
        and abandoned.get("draftDigest") == pending_identity.draft_digest
    )


def _planning_run_is_stale(
    planning_run: PlanningRunProjection,
    confirmed: dict[str, Any] | None,
) -> bool:
    """用当前 Formal digest 判定磁盘 PlanningRun 是否已经被后续提升淘汰。"""

    if confirmed is None:
        return False
    promoted_from = confirmed.get("confirmed_from")
    if (
        isinstance(promoted_from, dict)
        and promoted_from.get("planning_run_id") == planning_run.planning_run_id
    ):
        return True
    return planning_run.base_confirmed_plan_digest != build_task_plan_sha256(confirmed)


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
    lifecycle: ApplicationLifecycle | None,
    runtime_active: RuntimeActiveReader,
) -> PlanningRefreshState:
    """按 authoritative terminal marker/Pending→Active Run→Formal 解析刷新状态。"""

    confirmed, invalid_formal = _confirmed_plan(workspace)
    pending, pending_identity = _pending_plan(
        workspace,
        formal_exists=confirmed is not None,
    )
    planning_run = _planning_run(
        workspace,
        lower_priority_formal_exists=confirmed is not None,
    )
    abandoned = _abandoned_planning_result(lifecycle)

    if (
        pending is not None
        and not _pending_was_promoted(pending_identity, confirmed)
        and not _pending_was_abandoned(pending_identity, abandoned)
    ):
        workflow_run_id, thread_id = _matching_pending_execution(lifecycle, planning_run)
        return {
            "schemaVersion": "planning-refresh.v1",
            "source": "pending",
            "status": "awaiting_confirmation",
            "planningRunId": pending_identity.planning_run_id,
            "workflowRunId": workflow_run_id,
            "threadId": thread_id,
            "ownerSessionId": pending_identity.owner_session_id,
            "draftDigest": pending_identity.draft_digest,
            "buildExecutionScope": dict(pending_identity.build_execution_scope),
            "confirmation": _pending_confirmation(pending, pending_identity),
            "message": "已从 PendingPlan 恢复待确认任务规划。",
        }

    if abandoned is not None and (
        planning_run is None
        or planning_run.planning_run_id == abandoned["planningRunId"]
    ):
        formal_digest = build_task_plan_sha256(confirmed) if confirmed is not None else None
        # 只有 Formal 仍是该草稿冻结的 baseline 时 tombstone 才是当前事实；后续
        # ConfirmedPlan 会自然淘汰旧 Abandon，不需要兼容分支或历史迁移。
        if formal_digest == abandoned.get("baseConfirmedPlanDigest"):
            return {
                "schemaVersion": "planning-refresh.v1",
                "source": "abandoned",
                "status": "abandoned",
                "planningRunId": abandoned["planningRunId"],
                "workflowRunId": str(abandoned.get("workflowRunId") or ""),
                "draftDigest": abandoned["draftDigest"],
                "buildExecutionScope": dict(abandoned["buildExecutionScope"]),
                "message": "当前 Planning result 已放弃。",
            }

    if (
        planning_run is not None
        and planning_run.status == "active"
        and not _planning_run_is_stale(planning_run, confirmed)
    ):
        active = runtime_active(planning_run.workflow_run_id)
        return {
            "schemaVersion": "planning-refresh.v1",
            "source": "active_planning_run",
            "status": "planning" if active else "planning_run_interrupted",
            "planningRunId": planning_run.planning_run_id,
            "workflowRunId": planning_run.workflow_run_id,
            "threadId": planning_run.thread_id,
            "buildExecutionScope": dict(planning_run.build_execution_scope),
            "dagGeneration": project_planning_run_progress(planning_run),
            "message": (
                "已恢复当前 PlanningRun 进度。"
                if active
                else "Backend 已重启或运行已中断；Candidate 不会自动续跑。"
            ),
        }

    if confirmed is not None:
        promoted_from = confirmed.get("confirmed_from")
        promoted_from = promoted_from if isinstance(promoted_from, dict) else {}
        return {
            "schemaVersion": "planning-refresh.v1",
            "source": "confirmed_plan",
            "status": "confirmed",
            "planningRunId": str(promoted_from.get("planning_run_id") or ""),
            "draftDigest": str(promoted_from.get("draft_digest") or ""),
            "buildExecutionScope": dict(confirmed.get("build_execution_scope") or {}),
            "confirmedPlanDigest": build_task_plan_sha256(confirmed),
            "message": "已恢复当前 ConfirmedPlan。",
        }

    if invalid_formal:
        raise ValueError("当前 build-task-plan.json 不是合法 ConfirmedPlan。")
    return {
        "schemaVersion": "planning-refresh.v1",
        "source": "none",
        "status": "idle",
        "message": "当前没有可恢复的 Planning 或 Confirmation 状态。",
    }
