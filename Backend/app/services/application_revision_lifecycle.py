"""formal revision 影响确认、唯一 lease 与一次性 continuation 生命周期。"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.domain.application_revision import (
    ActiveFormalRevision,
    EarliestRevisionArtifact,
    FormalRevisionBranch,
    PendingRevisionImpact,
    RevisionImpact,
    RevisionTarget,
)
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    execution_belongs_to_active_revision,
    load_application_lifecycle,
    write_application_lifecycle,
)
from app.services.artifact_invalidation import canonical_sha256
from app.domain.application_lifecycle import (
    ApplicationInitialization,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    utc_now,
)


def register_revision_impact(
    workspace: str | Path,
    *,
    interaction_id: str,
    source_thread_id: str,
    source_run_id: str,
    request: str,
    target: RevisionTarget,
    impact: RevisionImpact,
) -> PendingRevisionImpact:
    """只登记待确认影响范围，不创建 changeId、draft 或 formal planning lease。"""

    current = _required_lifecycle(workspace)
    active = current.active_formal_revision
    orphaned_failed_revision = bool(
        active is not None
        and active.status == "failed"
        and not any(
            execution_belongs_to_active_revision(current, execution)
            for execution in current.active_executions.values()
        )
    )
    if active is not None and not orphaned_failed_revision:
        raise ApplicationLifecycleConflictError("当前 application 已有 formal revision 正在进行。")
    pending = PendingRevisionImpact(
        interactionId=interaction_id,
        sourceThreadId=source_thread_id,
        sourceRunId=source_run_id,
        request=request,
        target=target,
        impact=impact,
        basedOnLifecycleRevision=current.revision + 1,
    )
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": current.revision + 1,
            **({"active_formal_revision": None} if orphaned_failed_revision else {}),
            "pending_revision_impact": pending,
        }
    )
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)
    return pending


def submit_revision_impact(
    workspace: str | Path,
    *,
    interaction_id: str,
    decision: Literal["approved", "rejected"],
) -> ActiveFormalRevision | None:
    """一次性消费当前 impact；批准时才创建 change 和 application 级 formal lease。"""

    current = _required_lifecycle(workspace)
    pending = current.pending_revision_impact
    if pending is None or pending.interaction_id != interaction_id:
        raise ApplicationLifecycleConflictError("影响范围确认已过期或不属于当前 application。")
    if pending.based_on_lifecycle_revision != current.revision:
        raise ApplicationLifecycleConflictError("影响范围确认基于过期 lifecycle revision。")
    if current.active_formal_revision is not None:
        raise ApplicationLifecycleConflictError("当前 application 已有 formal revision 正在进行。")
    active: ActiveFormalRevision | None = None
    if decision == "approved":
        planning_thread = str(current.initialization.thread_id or "").strip()
        if not planning_thread:
            raise ApplicationLifecycleConflictError("formal revision 缺少原 application planning thread。")
        current_artifact = pending.impact.earliest_artifact.value
        remaining_artifacts = [
            artifact
            for artifact in dict.fromkeys(pending.impact.affected_artifacts)
            if artifact in {item.value for item in EarliestRevisionArtifact}
            and artifact != current_artifact
        ]
        active = ActiveFormalRevision(
            changeId=f"chg_{uuid4().hex}",
            formalBranch=pending.impact.formal_branch,
            sourceThreadId=pending.source_thread_id,
            sourceRunId=pending.source_run_id,
            request=pending.request,
            target=pending.target,
            impactInteractionId=pending.interaction_id,
            planningThreadId=planning_thread,
            status=(
                "design_planning"
                if pending.impact.formal_branch
                == FormalRevisionBranch.DESIGN_STAGE_REVISION
                else "drafting"
            ),
            # currentArtifact 是已确认影响范围选出的唯一设计/草稿起点；
            # remainingArtifacts 只作生命周期展示，不允许客户端反向改写起点。
            currentArtifact=current_artifact,
            remainingArtifacts=remaining_artifacts,
        )
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": current.revision + 1,
            "pending_revision_impact": None,
            "active_formal_revision": active,
        }
    )
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)
    return active


def start_agent_settings_revision(
    workspace: str | Path,
    *,
    agent_id: str,
    source_thread_id: str,
    source_run_id: str,
) -> ActiveFormalRevision:
    """为可视化 Agent Settings 修改直接创建唯一 TechnicalPlan 正式修订。"""

    current = _required_lifecycle(workspace)
    if (
        current.initialization.stage != ApplicationLifecycleStage.READY_FOR_WORKBENCH
        or current.initialization.status != ApplicationLifecycleStatus.COMPLETED
    ):
        raise ApplicationLifecycleConflictError(
            "只有已完成创建并进入开发工作台的应用才能修改 Agent Settings。"
        )
    if current.active_formal_revision is not None:
        raise ApplicationLifecycleConflictError("当前 application 已有 formal revision 正在进行。")
    if current.pending_revision_impact is not None:
        raise ApplicationLifecycleConflictError("当前 application 有待确认的修改影响范围。")
    if current.resource_locks.application is not None:
        raise ApplicationLifecycleConflictError("当前 application 正在执行其他开发任务。")
    resource_locks = current.resource_locks
    agent_lock = resource_locks.agents.get(agent_id)
    agent_execution = (
        current.active_executions.get(agent_lock.run_id) if agent_lock is not None else None
    )
    if agent_lock is not None and agent_execution is None:
        # 锁必须由 active execution 持有；进程异常留下的孤立 Agent 锁没有可恢复任务，
        # 在同一次 lifecycle CAS 中定向清理，不能让配置入口永久不可用。
        resource_locks = resource_locks.model_copy(
            update={
                "agents": {
                    key: value
                    for key, value in resource_locks.agents.items()
                    if key != agent_id
                }
            }
        )
    elif agent_execution is not None:
        status_message = {
            "running": "正在运行",
            "stopping": "正在停止",
            "awaiting_user": "正在等待用户确认",
            "failed": "执行失败但尚未结束",
            "stopped": "已停止但尚未结束",
            "completed": "已完成但尚未释放",
        }.get(agent_execution.status.value, "尚未结束")
        raise ApplicationLifecycleConflictError(
            f"当前智能体开发任务{status_message}，请先结束该任务再修改配置。"
        )
    planning_thread_id = str(current.initialization.thread_id or "").strip()
    if not planning_thread_id:
        raise ApplicationLifecycleConflictError("Agent Settings 修订缺少原 application planning thread。")
    interaction_id = f"agent-settings:{uuid4().hex}"
    active = ActiveFormalRevision(
        changeId=f"chg_{uuid4().hex}",
        formalBranch=FormalRevisionBranch.WORKBENCH_PLAN_REVISION,
        sourceThreadId=source_thread_id,
        sourceRunId=source_run_id,
        request=f"修改智能体 {agent_id} 的 Agent Settings。",
        target=RevisionTarget(type="agent", agentId=agent_id),
        impactInteractionId=interaction_id,
        planningThreadId=planning_thread_id,
        status="drafting",
        currentArtifact="technical-plan",
        remainingArtifacts=[],
    )
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": current.revision + 1,
            "active_formal_revision": active,
            "resource_locks": resource_locks,
        }
    )
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)
    return active


def issue_revision_continuation(
    workspace: str | Path,
    *,
    change_id: str,
    technical_plan_path: str | Path,
    source_execution_run_id: str | None = None,
) -> tuple[str, ActiveFormalRevision]:
    """为已确认 TechnicalPlan 签发一次性 continuation，并可选记录来源 execution。"""

    current = _required_lifecycle(workspace)
    active = current.active_formal_revision
    if (
        active is None
        or active.change_id != change_id
        or active.formal_branch
        not in {
            FormalRevisionBranch.DESIGN_STAGE_REVISION,
            FormalRevisionBranch.WORKBENCH_PLAN_REVISION,
        }
    ):
        raise ApplicationLifecycleConflictError("没有匹配的 active formal revision。")
    if active.continuation_token_sha256 is not None:
        raise ApplicationLifecycleConflictError("当前 formal revision 已签发 continuation。")
    # application_planning 是独立的规划 Graph，不登记工作台 execution；
    # continuation 只在存在真实来源 execution 时保留可选的原子接管信息。
    normalized_source_run_id = str(source_execution_run_id or "").strip() or None
    technical_plan_sha256 = canonical_sha256(technical_plan_path)
    token = secrets.token_urlsafe(48)
    next_revision = current.revision + 1
    next_active = active.model_copy(
        update={
            "status": "continuation_ready",
            "current_artifact": "technical-plan",
            "technical_plan_sha256": technical_plan_sha256,
            "continuation_token_sha256": _token_sha256(token),
            "continuation_lifecycle_revision": next_revision,
            "continuation_source_run_id": normalized_source_run_id,
        }
    )
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": next_revision,
            "active_formal_revision": next_active,
        }
    )
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)
    return token, next_active


def consume_revision_continuation(
    workspace: str | Path,
    *,
    change_id: str,
    token: str,
    technical_plan_path: str | Path,
) -> ActiveFormalRevision:
    """验证 application/change/TechnicalPlan/lifecycle 绑定并一次性消费 continuation。"""

    current = _required_lifecycle(workspace)
    active = current.active_formal_revision
    if active is None or active.change_id != change_id:
        raise ApplicationLifecycleConflictError("continuation changeId 不匹配。")
    if active.status != "continuation_ready" or active.continuation_consumed_at is not None:
        raise ApplicationLifecycleConflictError("continuation 已消费或当前不可用。")
    if active.continuation_lifecycle_revision != current.revision:
        raise ApplicationLifecycleConflictError("continuation 绑定的 lifecycle revision 已变化。")
    if not secrets.compare_digest(
        str(active.continuation_token_sha256 or ""),
        _token_sha256(token),
    ):
        raise ApplicationLifecycleConflictError("continuation token 无效。")
    if active.technical_plan_sha256 != canonical_sha256(technical_plan_path):
        raise ApplicationLifecycleConflictError("TechnicalPlan 已变化，continuation 作废。")
    next_active = active.model_copy(
        update={
            "status": "building",
            "continuation_token_sha256": None,
            "continuation_consumed_at": utc_now(),
        }
    )
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": current.revision + 1,
            "active_formal_revision": next_active,
            # READY_FOR_WORKBENCH 在这里仅恢复“应用早已完成首次创建”的终态，
            # 不经过 generating_application_template_files，也不代表重新生成模板。
            # 后续当前节点由 activeFormalRevision/activeExecutions 驱动为 DAG 与 Build。
            "initialization": ApplicationInitialization(
                stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                status=ApplicationLifecycleStatus.COMPLETED,
                threadId=active.planning_thread_id,
            ),
        }
    )
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)
    return next_active


def update_active_revision_progress(
    workspace: str | Path,
    *,
    change_id: str,
    status: Literal["drafting", "awaiting_user", "building", "stopped", "failed"],
    current_artifact: str | None,
    remaining_artifacts: list[str] | None = None,
) -> ActiveFormalRevision:
    """以 lifecycle CAS 更新当前 formal revision 的草稿/构建收口进度。"""

    current = _required_lifecycle(workspace)
    active = current.active_formal_revision
    if active is None or active.change_id != change_id:
        raise ApplicationLifecycleConflictError("formal revision changeId 已过期。")
    next_active = active.model_copy(
        update={
            "status": status,
            "current_artifact": current_artifact,
            **(
                {"remaining_artifacts": list(remaining_artifacts)}
                if remaining_artifacts is not None
                else {}
            ),
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
    return next_active


def discard_active_revision(workspace: str | Path, *, change_id: str) -> None:
    """在当前草稿已删除后释放 application 级 formal revision lease。"""

    current = _required_lifecycle(workspace)
    active = current.active_formal_revision
    if active is None or active.change_id != change_id:
        raise ApplicationLifecycleConflictError("formal revision changeId 已过期。")
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": current.revision + 1,
            "active_formal_revision": None,
        }
    )
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)


def complete_active_revision(workspace: str | Path) -> str | None:
    """在最终验收完成后释放 active formal revision，并返回已完成 changeId。"""

    current = _required_lifecycle(workspace)
    active = current.active_formal_revision
    if active is None:
        return None
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": current.revision + 1,
            "active_formal_revision": None,
        }
    )
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)
    return active.change_id


def _required_lifecycle(workspace: str | Path):
    """读取已初始化 lifecycle，并在缺失时给出稳定业务错误。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        raise ApplicationLifecycleConflictError("application lifecycle 尚未初始化。")
    return current


def _token_sha256(token: str) -> str:
    """只持久化不透明 token 的 SHA-256，避免 token 出现在 lifecycle 投影。"""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
