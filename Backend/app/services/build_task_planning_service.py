"""面向 production Workflow adapter 的 Mainline DAG Planning 业务边界。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import logging
from typing import Any, Literal
from uuid import uuid4

from pydantic import model_validator

from app.config import Settings
from app.services.build_task_plan_lifecycle import DraftIdentity
from app.services.dag_planning_inputs import MainlinePlanningInputs
from app.services.dag_planning_orchestrator import (
    DagPlanningError,
    ValidatedAssembledPlan,
    plan_dag_sequential,
)
from app.services.planning_recovery_contracts import (
    PlanningRecoverySnapshot,
    build_planning_recovery_snapshot,
)
from app.services.planning_frozen import (
    FrozenJsonObject,
    FrozenPlanningModel,
    plain_json,
)
from app.services.planning_run_controller import SnapshotPublisher
from app.services.unit_generation_contracts import (
    UnitGenerationAttemptResult,
    UnitGenerationPolicy,
)
from app.workspace.task_documents import (
    build_planning_provenance,
    build_task_plan_lifecycle_lock,
    load_pending_build_task_plan,
    validate_pending_self_digest,
    write_pending_build_task_plan_atomic,
)
from app.workspace.planning_run_documents import load_planning_run
from app.workspace.planning_recovery_documents import (
    delete_planning_recovery,
    load_planning_recovery,
    write_planning_recovery_atomic,
)
from app.workspace.spec_documents import workspace_root


_LOGGER = logging.getLogger(__name__)


class PendingPlanPersistenceResult(FrozenPlanningModel):
    """返回现有 Pending writer 落盘后自校验通过的内容与身份。"""

    pending_plan_path: str
    pending_plan: FrozenJsonObject
    draft_identity: DraftIdentity


class MainlinePlanningResult(FrozenPlanningModel):
    """返回已验证 PlanningRun、PendingPlan 及其唯一草稿身份。"""

    planning_run_id: str
    planning_status: Literal["validated"] = "validated"
    terminal_status: Literal["pending_confirmation"] = "pending_confirmation"
    pending_plan_path: str
    pending_plan: FrozenJsonObject
    draft_identity: DraftIdentity
    validated_assembled_plan: ValidatedAssembledPlan

    @model_validator(mode="after")
    def validate_identity(self) -> "MainlinePlanningResult":
        """拒绝 Run、Pending 与结果 DTO 之间的身份错配。"""

        run = self.validated_assembled_plan.planning_run
        if (
            self.planning_run_id != run.planning_run_id
            or self.draft_identity.planning_run_id != run.planning_run_id
            or self.pending_plan.get("draft_identity")
            != self.draft_identity.model_dump(mode="json")
        ):
            raise ValueError("Mainline Planning 结果中的 Run 与 DraftIdentity 不一致。")
        return self


def _new_planning_run_id() -> str:
    """为一次 mainline service 调用分配后端拥有的 PlanningRun ID。"""

    return f"planning-{uuid4().hex}"


def _load_recovery_for_retry(
    workspace_state: Mapping[str, Any],
    source_workflow_run_id: str | None,
) -> PlanningRecoverySnapshot | None:
    """按明确 source Workflow ID 尝试读取 Recovery，存储异常只降级为 fresh generation。"""

    source_id = str(source_workflow_run_id or "").strip()
    if not source_id:
        return None
    try:
        return load_planning_recovery(dict(workspace_state), source_id)
    except Exception:
        # Recovery 是优化状态；损坏、摘要错误、IO 异常和 schema 失效都不能覆盖新 Run。
        _LOGGER.warning(
            "Ignoring invalid Planning Recovery Snapshot for source_workflow_run_id=%s",
            source_id,
            exc_info=True,
        )
        return None


def persist_planning_recovery_if_applicable(
    state: Mapping[str, Any],
    error: DagPlanningError,
    *,
    owner_session_id: str,
) -> bool:
    """尽力保存支持的 failed Run Snapshot，但绝不覆盖原始 Planning failure。

    返回 True 仅表示 Snapshot writer 正常返回；不适用、没有可恢复 Candidate
    或写入失败均返回 False，调用方不得据此改变原始 Planning failure。
    """

    snapshot = error.snapshot
    if snapshot is None:
        return False
    failure = snapshot.failure
    if (
        failure is None
        or failure.code != "UNIT_GENERATION_INFRASTRUCTURE_FAILURE"
        or failure.level != "system"
        or failure.category != "infrastructure"
        or failure.retryable
    ):
        return False
    try:
        recovery = build_planning_recovery_snapshot(
            snapshot,
            owner_session_id=owner_session_id,
        )
        if recovery is None:
            return False
        write_planning_recovery_atomic(dict(state), recovery)
        return True
    except Exception:
        # Recovery 只是 retry optimization state；写入、序列化或路径失败不能改写
        # 已经由 PlanningRun/Controller 确定的原始 UNIT_GENERATION failure。
        _LOGGER.warning(
            "Failed to persist Planning Recovery Snapshot for workflow_run_id=%s",
            snapshot.workflow_run_id,
            exc_info=True,
        )
        return False


def _best_effort_delete_source_recovery(
    state: Mapping[str, Any],
    source_workflow_run_id: str | None,
    *,
    current_workflow_run_id: str,
) -> bool:
    """按 exact source ID 删除 Retry 来源，删除失败只留下可后续清理的残留。"""

    source_id = str(source_workflow_run_id or "").strip()
    # 防止错误调用把当前 R2 的 Recovery 写入路径误当成 R1 再删除。
    if not source_id or source_id == current_workflow_run_id:
        return False
    try:
        return delete_planning_recovery(dict(state), source_id)
    except Exception:
        _LOGGER.warning(
            "Failed to delete source Planning Recovery Snapshot for workflow_run_id=%s",
            source_id,
            exc_info=True,
        )
        return False


def _persist_validated_pending_plan(
    state: dict[str, Any],
    planned: ValidatedAssembledPlan,
    *,
    owner_session_id: str,
) -> PendingPlanPersistenceResult:
    """通过唯一 Pending storage authority 写入，并在同一锁内回读身份。"""

    run = planned.planning_run
    with build_task_plan_lifecycle_lock(workspace_root(state)):
        persisted_run = load_planning_run(state)
        if (
            persisted_run is None
            or persisted_run.get("planning_run_id") != run.planning_run_id
        ):
            raise RuntimeError("写入 PendingPlan 前无法确认当前 PlanningRun identity。")
        if persisted_run.get("status") == "cancelled":
            # Cancel 的终态提交先取得共享锁时，绝不允许旧的内存成功结果随后发布 Pending。
            raise asyncio.CancelledError
        if (
            persisted_run.get("status") != "active"
            or persisted_run.get("phase") != "persisting_pending"
            or persisted_run.get("revision") != run.revision
        ):
            raise RuntimeError(
                "写入 PendingPlan 前 PlanningRun 已不处于当前 active/persisting_pending 状态。"
            )
        assembled_plan = plain_json(planned.assembly.assembled_plan)
        planning_provenance = build_planning_provenance(
            assembled_plan,
            planned.assembly.retained_task_ids,
            planned.assembly.review_task_ids,
            planned.assembly.reused_task_ids,
            planned.assembly.platform_task_ids,
        )
        pending_path = write_pending_build_task_plan_atomic(
            state,
            assembled_plan,
            owner_session_id=owner_session_id,
            planning_run_id=run.planning_run_id,
            workflow_run_id=run.workflow_run_id,
            base_confirmed_plan_digest=run.base_confirmed_plan_digest,
            input_fingerprint=run.input_fingerprint,
            build_execution_scope=plain_json(run.build_execution_scope),
            created_at=run.updated_at,
            planning_provenance=planning_provenance,
        )
        pending = load_pending_build_task_plan(state)
        if pending is None:
            raise RuntimeError("Mainline Planning 成功后没有生成 PendingPlan。")
        identity = validate_pending_self_digest(pending)
    if identity.planning_run_id != run.planning_run_id:
        raise RuntimeError("PendingPlan 的 PlanningRun identity 与本次规划不一致。")
    return PendingPlanPersistenceResult(
        pending_plan_path=pending_path,
        pending_plan=pending,
        draft_identity=identity,
    )


async def run_mainline_planning(
    inputs: MainlinePlanningInputs,
    *,
    workspace_state: Mapping[str, Any],
    policy: UnitGenerationPolicy,
    settings: Settings | None = None,
    generate_once: Callable[
        ..., Awaitable[UnitGenerationAttemptResult]
    ] | None = None,
    publish: SnapshotPublisher | None = None,
    recovery_source_workflow_run_id: str | None = None,
) -> MainlinePlanningResult:
    """执行完整 PlanningRun，并仅在全局验证成功后原子写入 PendingPlan。

    Workflow adapter 只提供已确认的正式上下文与运行身份；本服务拥有 PlanningRun
    身份分配、Scheduler orchestration 和 Pending persistence 顺序。异常与取消均原样
    向上传播，且不会执行 Pending writer。
    """

    frozen = MainlinePlanningInputs.model_validate(inputs)
    planning_run_id = _new_planning_run_id()
    recovery_snapshot = _load_recovery_for_retry(
        workspace_state,
        recovery_source_workflow_run_id,
    )
    try:
        planned = await plan_dag_sequential(
            frozen.sequential_inputs(),
            workspace_state=workspace_state,
            planning_run_id=planning_run_id,
            workflow_run_id=frozen.workflow_run_id,
            thread_id=frozen.thread_id,
            policy=policy,
            settings=settings,
            generate_once=generate_once,
            publish=publish,
            recovery_snapshot=recovery_snapshot,
        )
    except DagPlanningError as exc:
        recovery_persisted = persist_planning_recovery_if_applicable(
            workspace_state,
            exc,
            owner_session_id=frozen.owner_session_id,
        )
        if recovery_persisted:
            _best_effort_delete_source_recovery(
                workspace_state,
                recovery_source_workflow_run_id,
                current_workflow_run_id=frozen.workflow_run_id,
            )
        raise
    persisted = _persist_validated_pending_plan(
        dict(workspace_state),
        planned,
        owner_session_id=frozen.owner_session_id,
    )
    result = MainlinePlanningResult(
        planning_run_id=planning_run_id,
        pending_plan_path=persisted.pending_plan_path,
        pending_plan=persisted.pending_plan,
        draft_identity=persisted.draft_identity,
        validated_assembled_plan=planned,
    )
    # Pending writer、回读校验和结果 DTO 构造全部成功后，才允许清理 R1。
    _best_effort_delete_source_recovery(
        workspace_state,
        recovery_source_workflow_run_id,
        current_workflow_run_id=frozen.workflow_run_id,
    )
    return result
