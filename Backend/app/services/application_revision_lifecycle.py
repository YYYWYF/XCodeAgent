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
    handoff_application_planning_run_for_recovery,
    load_application_lifecycle,
    persist_application_lifecycle_transition,
    restart_application_planning_lifecycle,
    write_application_lifecycle,
)
from app.services.artifact_invalidation import canonical_sha256
from app.services.workflow_quality_results import pending_quality_results
from app.services.access_control_intent import has_explicit_capability_change
from app.services.application_config_change_resolver import (
    resolve_application_config_changes,
)
from app.services.application_config import (
    apply_application_config_changes,
    preview_application_config_changes,
    read_application_config,
)
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
    pending_config_changes = _resolve_pending_application_config_changes(workspace, request)
    pending = PendingRevisionImpact(
        changeId=f"chg_{uuid4().hex}",
        interactionId=interaction_id,
        sourceThreadId=source_thread_id,
        sourceRunId=source_run_id,
        request=request,
        target=target,
        impact=impact,
        pendingApplicationConfigChanges=pending_config_changes,
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
            pendingApplicationConfigChanges=pending.pending_application_config_changes,
        )
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": current.revision + 1,
            "pending_revision_impact": None,
            "active_formal_revision": active,
            **({"extensions": pending_quality_results(current.extensions)} if active is not None else {}),
        }
    )
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)
    if active is not None and not _awaits_initial_administrator_subjects(active):
        # 配置确认已经完成，必须在任何正式重规划开始前提交当前唯一事实源。
        return commit_active_revision_application_config_changes(
            workspace,
            change_id=active.change_id,
        )
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
                "extensions": pending_quality_results(current.extensions),
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
    target: RevisionTarget,
) -> None:
    """验证技术规划仍属于原正式修订，允许当前审阅门和修改要求变化。"""

    if (
        active.change_id != change_id
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
    repair_source_run_id: str | None = None,
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
        initialization.stage is ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED
        and repair_source_run_id
        and current.active_run_id == repair_source_run_id
    ):
        # 模板失败后明确修改已存在的技术规划，沿同一来源回到生成，不能重放旧确认。
        return restart_application_planning_lifecycle(
            workspace, stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
            active_run_id=expected_run_id,
        )
    if (
        initialization.stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN
        and initialization.status is ApplicationLifecycleStatus.RUNNING
    ):
        if change_id:
            active = current.active_formal_revision
            if active is None or active.change_id != change_id:
                raise ApplicationLifecycleConflictError(
                    "TechnicalPlan generation 缺少匹配的 active formal revision。"
                )
        if current.active_run_id != expected_run_id:
            source_run_id = str(repair_source_run_id or "").strip()
            if not source_run_id or current.active_run_id != source_run_id:
                raise ApplicationLifecycleConflictError(
                    "TechnicalPlan generation activeRunId 与当前 transaction 不匹配。"
                )
            # 只消费当前失败审阅门提交的来源，复用原归属校验及 revision CAS，不猜测旧 Run。
            current = handoff_application_planning_run_for_recovery(
                workspace, source_run_id=source_run_id, new_run_id=expected_run_id,
                thread_id=expected_thread_id, expected_lifecycle_revision=current.revision,
            )
        return current

    if change_id:
        active = current.active_formal_revision
        if (
            active is None
            or active.change_id != change_id
            or not (
                active.formal_branch is FormalRevisionBranch.WORKBENCH_PLAN_REVISION
                or (
                    active.formal_branch is FormalRevisionBranch.DESIGN_STAGE_REVISION
                    and active.status == "design_planning"
                )
            )
        ):
            raise ApplicationLifecycleConflictError(
                "TechnicalPlan formal revision 不在允许的 lifecycle predecessor。"
            )
        # 设计修订仍经过显式 planning_stage_entry 门禁；该门禁已消费后，
        # generation 入口可从 awaiting_user 推进，不能要求先有旧技术确认。
        design_stage_entry = (
            active.formal_branch is FormalRevisionBranch.DESIGN_STAGE_REVISION
            and initialization.stage is ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY
            and initialization.status is ApplicationLifecycleStatus.AWAITING_USER
        )
        if not design_stage_entry and initialization.stage not in {
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

    from app.services.revision_continuation_handoff import consumed_continuation_state

    current = _required_lifecycle(workspace)
    updated = consumed_continuation_state(current, change_id=change_id, token=token,
                                         technical_plan_path=technical_plan_path)
    updated = updated.model_copy(update={"updated_at": utc_now(), "revision": current.revision + 1})
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)
    return updated.active_formal_revision


def validate_revision_continuation(workspace: str | Path, **identity) -> ActiveFormalRevision:
    """请求解析只验证原续接合同，任何入场前错误都不提前消费凭据。"""
    from app.services.revision_continuation_handoff import validated_continuation

    return validated_continuation(_required_lifecycle(workspace), **identity)


def update_active_revision_progress(
    workspace: str | Path,
    *,
    change_id: str,
    status: Literal[
        "design_planning",
        "drafting",
        "awaiting_user",
        "template_reconciling",
        "template_reconcile_failed",
        "building",
        "stopped",
        "failed",
    ],
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


def commit_active_revision_application_config_changes(
    workspace: str | Path,
    *,
    change_id: str,
) -> ActiveFormalRevision:
    """在重规划入口前提交当前 Revision 的配置 Delta，并清空已提交提案。"""

    current = _required_lifecycle(workspace)
    active = current.active_formal_revision
    if active is None or active.change_id != change_id:
        raise ApplicationLifecycleConflictError("formal revision changeId 已过期。")
    if (
        not active.pending_application_config_changes
        and not active.pending_initial_administrator_subjects
    ):
        return active
    apply_application_config_changes(
        workspace,
        changes=active.pending_application_config_changes,
        initial_administrator_subjects=(
            active.pending_initial_administrator_subjects or None
        ),
        allow_existing_targets=True,
    )
    next_active = active.model_copy(
        update={
            "pending_application_config_changes": [],
            "pending_initial_administrator_subjects": [],
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


def stage_active_revision_initial_administrator_subjects(
    workspace: str | Path,
    *,
    subjects: list[str],
) -> ActiveFormalRevision | None:
    """确认权限管理员后立即提交完整配置，再允许后续正式重规划。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        return None
    active = current.active_formal_revision
    if active is None:
        return None
    normalized_subjects = _normalize_initial_administrator_subjects(subjects)
    next_active = active.model_copy(
        update={"pending_initial_administrator_subjects": normalized_subjects}
    )
    updated = current.model_copy(
        update={
            "updated_at": utc_now(),
            "revision": current.revision + 1,
            "active_formal_revision": next_active,
        }
    )
    write_application_lifecycle(workspace, updated, expected_revision=current.revision)
    return commit_active_revision_application_config_changes(
        workspace,
        change_id=next_active.change_id,
    )


def effective_active_revision_application_config(
    workspace: str | Path,
    *,
    change_id: str | None = None,
) -> dict:
    """返回当前已提交配置；仅管理员澄清尚未完成时保留临时预览。"""

    current = load_application_lifecycle(workspace)
    active = current.active_formal_revision if current is not None else None
    if active is None:
        return read_application_config(workspace)
    if change_id is not None and active.change_id != change_id:
        raise ApplicationLifecycleConflictError("formal revision changeId 已过期。")
    return preview_application_config_changes(
        workspace,
        changes=active.pending_application_config_changes,
        initial_administrator_subjects=(
            active.pending_initial_administrator_subjects or None
        ),
    )


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


def _resolve_pending_application_config_changes(
    workspace: str | Path,
    request: str,
) -> list:
    """仅为明确能力开关请求读取 canonical 配置并生成当前 Revision 的待确认 Delta。"""

    if not has_explicit_capability_change(request):
        return []
    return resolve_application_config_changes(request, workspace_root=workspace)


def _awaits_initial_administrator_subjects(active: ActiveFormalRevision) -> bool:
    """仅在启用权限但尚未收集管理员时延后提交，避免写入不完整配置。"""

    enabling_authorization = any(
        change.path == "authorization.enabled" and change.to_value is True
        for change in active.pending_application_config_changes
    )
    return enabling_authorization and not active.pending_initial_administrator_subjects


def _normalize_initial_administrator_subjects(subjects: list[str]) -> list[str]:
    """规范化正式修订中的管理员标识，并拒绝空值和占位符。"""

    normalized: list[str] = []
    for value in subjects:
        subject = str(value).strip()
        if not subject or subject in normalized:
            continue
        if subject == "current-user":
            raise ApplicationLifecycleConflictError("初始管理员必须使用真实 subjectId，不能使用 current-user。")
        normalized.append(subject)
    if not normalized:
        raise ApplicationLifecycleConflictError("启用权限控制时至少需要一个真实初始管理员 subjectId。")
    return normalized


def _token_sha256(token: str) -> str:
    """只持久化不透明 token 的 SHA-256，避免 token 出现在 lifecycle 投影。"""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
