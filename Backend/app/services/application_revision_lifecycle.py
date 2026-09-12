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
    _application_lifecycle_lock,
    application_lifecycle_path,
    execution_belongs_to_active_revision,
    load_application_lifecycle,
    persist_application_lifecycle_transition,
    restart_application_planning_lifecycle,
    write_application_lifecycle,
)
from app.services.artifact_invalidation import canonical_sha256
from app.domain.application_lifecycle import (
    ApplicationInitialization,
    ApplicationLifecycle,
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
    """登记待确认影响范围并分配跨 admission/replay 稳定的 changeId。"""

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
        changeId=f"chg_{uuid4().hex}",
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
        active = _build_active_formal_revision(current, pending)
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


def ensure_revision_impact_approved(
    workspace: str | Path,
    *,
    change_id: str,
    interaction_id: str,
    request: str,
    expected_branch: FormalRevisionBranch,
    target: RevisionTarget | None = None,
) -> ActiveFormalRevision:
    """幂等批准同一 FormalRevision transaction，并拒绝任何事实漂移。"""

    normalized_branch = FormalRevisionBranch(expected_branch)
    current = _required_lifecycle(workspace)
    active = current.active_formal_revision
    if active is not None:
        if (
            active.change_id != change_id
            or active.impact_interaction_id != interaction_id
            or active.request != request
            or active.formal_branch is not normalized_branch
            or (target is not None and active.target != target)
        ):
            raise ApplicationLifecycleConflictError(
                "active formal revision 与当前 TechnicalPlan revision intent 不匹配。"
            )
        return active

    pending = current.pending_revision_impact
    if (
        pending is None
        or pending.change_id != change_id
        or pending.interaction_id != interaction_id
        or pending.request != request
        or pending.impact.formal_branch is not normalized_branch
        or (target is not None and pending.target != target)
    ):
        raise ApplicationLifecycleConflictError(
            "pending revision impact 与当前 TechnicalPlan revision intent 不匹配。"
        )
    approved = submit_revision_impact(
        workspace,
        interaction_id=interaction_id,
        decision="approved",
    )
    if approved is None:
        raise ApplicationLifecycleConflictError("revision impact 未批准。")
    return approved


def begin_technical_plan_revision_generation(
    workspace: str | Path,
    *,
    change_id: str,
    interaction_id: str,
    request: str,
    target: RevisionTarget,
    thread_id: str,
    active_run_id: str,
) -> ApplicationLifecycle:
    """在一次 lifecycle 锁和 CAS 写入中批准并启动 TechnicalPlan Formal Revision。"""

    path = application_lifecycle_path(workspace)
    with _application_lifecycle_lock(path):
        current = _required_lifecycle(workspace)
        expected_run_id = str(active_run_id or "").strip()
        expected_thread_id = str(thread_id or "").strip()
        if not expected_run_id or not expected_thread_id:
            raise ApplicationLifecycleConflictError(
                "TechnicalPlan formal revision 缺少 threadId 或 activeRunId。"
            )
        if current.initialization.thread_id != expected_thread_id:
            raise ApplicationLifecycleConflictError(
                "TechnicalPlan formal revision threadId 与 lifecycle 不匹配。"
            )

        active = current.active_formal_revision
        if active is not None:
            if current.pending_revision_impact is not None:
                raise ApplicationLifecycleConflictError(
                    "active formal revision 不能与 pending revision impact 同时存在。"
                )
            _assert_technical_plan_revision_identity(
                active=active,
                change_id=change_id,
                interaction_id=interaction_id,
                request=request,
                target=target,
            )
            if (
                current.initialization.stage
                is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN
                and current.initialization.status is ApplicationLifecycleStatus.RUNNING
                and current.active_run_id == expected_run_id
            ):
                return current
            if current.initialization.stage not in {
                ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
            }:
                raise ApplicationLifecycleConflictError(
                    "TechnicalPlan formal revision 当前阶段不允许进入 generation。"
                )
            if (
                current.initialization.stage is ApplicationLifecycleStage.READY_FOR_WORKBENCH
                and current.initialization.status is not ApplicationLifecycleStatus.COMPLETED
            ) or (
                current.initialization.stage
                is ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION
                and current.initialization.status is not ApplicationLifecycleStatus.AWAITING_USER
            ):
                raise ApplicationLifecycleConflictError(
                    "TechnicalPlan formal revision predecessor 状态不匹配。"
                )
            next_active = active
        else:
            pending = current.pending_revision_impact
            if pending is None:
                raise ApplicationLifecycleConflictError(
                    "TechnicalPlan formal revision 缺少 pending revision impact。"
                )
            if pending.based_on_lifecycle_revision not in {
                current.revision,
                current.revision - 1,
            }:
                raise ApplicationLifecycleConflictError(
                    "TechnicalPlan formal revision 基于过期 lifecycle revision。"
                )
            if (
                pending.based_on_lifecycle_revision == current.revision - 1
                and current.active_run_id != expected_run_id
            ):
                raise ApplicationLifecycleConflictError(
                    "TechnicalPlan formal revision ownership claim 与当前 run 不匹配。"
                )
            if (
                pending.change_id != change_id
                or pending.interaction_id != interaction_id
                or pending.request != request
                or pending.target != target
                or pending.impact.formal_branch
                is not FormalRevisionBranch.WORKBENCH_PLAN_REVISION
            ):
                raise ApplicationLifecycleConflictError(
                    "pending revision impact 与当前 TechnicalPlan revision intent 不匹配。"
                )
            if (
                current.initialization.stage is not ApplicationLifecycleStage.READY_FOR_WORKBENCH
                or current.initialization.status is not ApplicationLifecycleStatus.COMPLETED
            ):
                raise ApplicationLifecycleConflictError(
                    "TechnicalPlan formal revision 当前阶段不允许进入 generation。"
                )
            next_active = _build_active_formal_revision(current, pending)

        updated = current.model_copy(
            update={
                "updated_at": utc_now(),
                "revision": current.revision + 1,
                "initialization": ApplicationInitialization(
                    stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
                    status=ApplicationLifecycleStatus.RUNNING,
                    threadId=expected_thread_id,
                ),
                "active_run_id": expected_run_id,
                "pending_revision_impact": None,
                "active_formal_revision": next_active,
                "error": None,
            }
        )
        return write_application_lifecycle(
            workspace,
            updated,
            expected_revision=current.revision,
        )


def _build_active_formal_revision(
    current: ApplicationLifecycle,
    pending: PendingRevisionImpact,
) -> ActiveFormalRevision:
    """把已验证的 pending impact 转为唯一 active formal revision。"""

    planning_thread = str(current.initialization.thread_id or "").strip()
    if not planning_thread:
        raise ApplicationLifecycleConflictError(
            "formal revision 缺少原 application planning thread。"
        )
    current_artifact = pending.impact.earliest_artifact.value
    remaining_artifacts = [
        artifact
        for artifact in dict.fromkeys(pending.impact.affected_artifacts)
        if artifact in {item.value for item in EarliestRevisionArtifact}
        and artifact != current_artifact
    ]
    return ActiveFormalRevision(
        changeId=pending.change_id,
        formalBranch=pending.impact.formal_branch,
        sourceThreadId=pending.source_thread_id,
        sourceRunId=pending.source_run_id,
        request=pending.request,
        target=pending.target,
        impactInteractionId=pending.interaction_id,
        planningThreadId=planning_thread,
        status=(
            "design_planning"
            if pending.impact.formal_branch is FormalRevisionBranch.DESIGN_STAGE_REVISION
            else "drafting"
        ),
        # currentArtifact 是已确认影响范围选出的唯一设计/草稿起点；
        # remainingArtifacts 只作生命周期展示，不允许客户端反向改写起点。
        currentArtifact=current_artifact,
        remainingArtifacts=remaining_artifacts,
    )


def _assert_technical_plan_revision_identity(
    *,
    active: ActiveFormalRevision,
    change_id: str,
    interaction_id: str,
    request: str,
    target: RevisionTarget,
) -> None:
    """验证已批准 Formal Revision 与 TechnicalPlan begin 输入完全一致。"""

    if (
        active.change_id != change_id
        or active.impact_interaction_id != interaction_id
        or active.request != request
        or active.formal_branch is not FormalRevisionBranch.WORKBENCH_PLAN_REVISION
        or active.target != target
    ):
        raise ApplicationLifecycleConflictError(
            "active formal revision 与当前 TechnicalPlan revision intent 不匹配。"
        )


def ensure_technical_plan_generation_lifecycle(
    workspace: str | Path,
    *,
    active_run_id: str | None,
    thread_id: str | None = None,
    change_id: str | None = None,
) -> ApplicationLifecycle:
    """幂等推进 TechnicalPlan generation lifecycle，并验证 thread/run 所有权。"""

    current = _required_lifecycle(workspace)
    expected_run_id = str(active_run_id or "").strip()
    expected_thread_id = str(thread_id or "").strip()
    if not expected_run_id:
        raise ApplicationLifecycleConflictError("TechnicalPlan generation 缺少 active runId。")
    if expected_thread_id and current.initialization.thread_id != expected_thread_id:
        raise ApplicationLifecycleConflictError(
            "TechnicalPlan generation threadId 与 lifecycle 不匹配。"
        )

    initialization = current.initialization
    if (
        initialization.stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN
        and initialization.status is ApplicationLifecycleStatus.RUNNING
    ):
        if current.active_run_id != expected_run_id:
            raise ApplicationLifecycleConflictError(
                "TechnicalPlan generation activeRunId 与当前 transaction 不匹配。"
            )
        if change_id:
            active = current.active_formal_revision
            if active is None or active.change_id != change_id:
                raise ApplicationLifecycleConflictError(
                    "TechnicalPlan generation 缺少匹配的 active formal revision。"
                )
        return current

    if change_id:
        active = current.active_formal_revision
        if (
            active is None
            or active.change_id != change_id
            or active.formal_branch is not FormalRevisionBranch.WORKBENCH_PLAN_REVISION
        ):
            raise ApplicationLifecycleConflictError(
                "TechnicalPlan formal revision 不在允许的 lifecycle predecessor。"
            )
        if initialization.stage not in {
            ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
        }:
            raise ApplicationLifecycleConflictError(
                "TechnicalPlan formal revision 当前阶段不允许回到 generation。"
            )
        return restart_application_planning_lifecycle(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
            active_run_id=expected_run_id,
        )

    if (
        initialization.stage is ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY
        and initialization.status is ApplicationLifecycleStatus.AWAITING_USER
    ) or (
        initialization.stage
        is ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION
        and initialization.status is ApplicationLifecycleStatus.AWAITING_USER
    ):
        return persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
            status=ApplicationLifecycleStatus.RUNNING,
            active_run_id=expected_run_id,
        )
    raise ApplicationLifecycleConflictError(
        "TechnicalPlan generation lifecycle 不在允许的 predecessor。"
    )


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
