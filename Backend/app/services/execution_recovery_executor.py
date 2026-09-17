"""P0.3B Native Recovery 的 claim、lifecycle handoff 与 checkpoint fork。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation,
    ApplicationPlanningRecoveryBoundary,
    application_planning_sha256,
    parse_application_planning_boundary,
)
from app.domain.application_revision import RevisionTarget
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    NodeEntryBoundary,
    RecoveryAttempt,
    RecoveryAttemptStatus,
    RecoveryExecutionError,
    RecoveryLifecycleOwnershipMode,
    RecoveryPlan,
    WorkflowReentryPlan,
    WorkflowReentryReason,
    execution_failure_sha256,
)
from app.persistence.execution_recovery import (
    claim_recovery_finalization,
    claim_native_recovery_attempt,
    finish_execution_and_release_lease,
    get_execution,
    get_execution_lease,
    get_recovery_attempt,
    fail_recovery_attempt_prestart,
    insert_node_entry_boundary,
    update_recovery_attempt,
)
from app.services.application_lifecycle import (
    application_lifecycle_payload,
    claim_application_planning_run_for_recovery,
    handoff_application_planning_run_for_recovery,
    handoff_workbench_execution_for_recovery,
    load_application_lifecycle,
    resource_claims_for_run,
)
from app.services.backend_instance import current_backend_instance
from app.services.execution_recovery_source_admission import assess_recovery_source
from app.services.execution_recovery_lineage import (
    RecoveryLineageState,
    resolve_recovery_lineage_head,
)
from app.services.workspace_inspector import (
    INSPECTOR_SCHEMA_VERSION,
    snapshot_hash,
    workspace_inventory,
)
from app.services.execution_lease_heartbeat import (
    maintain_execution_heartbeat,
    stop_execution_heartbeat,
)
from app.workspace.run_lease import WorkspaceRunLease, workspace_run_leases
from app.services.workflow_reentry import (
    FailureTargetResolver,
    InterruptedTargetResolver,
    recovery_plan_from_reentry,
    semantic_context_sha256,
)
from app.workspace.workspace_snapshot_documents import load_workspace_snapshot_json


@dataclass(slots=True)
class NativeRecoveryRuntimeContext:
    """把恢复准备事实传给现有 Runtime stream，而不是伪装成普通用户请求。"""

    source_execution: DurableExecutionRecord
    child_execution: DurableExecutionRecord
    recovery_plan: RecoveryPlan
    new_run_id: str
    thread_id: str
    project_id: str | None
    workspace: str
    workflow_scope: str | None
    graph: Any
    fork_config: dict[str, Any]
    observation_config: dict[str, Any]
    fork_snapshot: Any
    lifecycle_payload: dict[str, Any] | None
    workspace_lease: WorkspaceRunLease | None
    observability: dict[str, Any]
    heartbeat_task: asyncio.Task[None] | None

    def workflow_inputs(self) -> dict[str, Any]:
        """生成 Runtime 内部使用的最小字段集合，不重新解析外部 workflow request。"""

        values = getattr(self.fork_snapshot, "values", {})
        values = values if isinstance(values, dict) else {}
        return {
            "request": "从 durable checkpoint 继续执行。",
            "selected_skills_error": None,
            "selected_skill_names": list(values.get("selected_skill_names") or []),
            "project_id": self.project_id or "",
            "workspace": self.workspace,
            "editor_mode": str(values.get("editor_mode") or "") or None,
            "workflow_scope": self.workflow_scope or "",
            "application_planning_interaction": None,
            "resume_from": "",
            "resume_values": {},
            "application_name": str(values.get("application_name") or "") or None,
            "workflow_debug_enabled": False,
            "thread_id": self.thread_id,
            "run_id": self.new_run_id,
            "plan_control_action": "",
            "cancel_run_id": "",
            "plan_control_run_id": "",
            "plan_control_planning_run_id": "",
            "plan_control_draft_digest": "",
        }


class WorkflowReentryExecutor:
    """统一校验并物化 Failure Retry 与 Formal Revision 的 Node 重入上下文。"""

    async def prepare_failure_retry(
        self,
        *,
        workspace: str,
        source_run_id: str,
        graph: Any,
        reentry_plan: WorkflowReentryPlan | None = None,
    ) -> NativeRecoveryRuntimeContext:
        """消费调用方已解析的 re-entry plan，并复用同一 claim、handoff 与 fork transaction。"""

        source = await get_execution(workspace, source_run_id)
        if source is None:
            raise RecoveryExecutionError("SOURCE_EXECUTION_NOT_FOUND", "source execution 不存在。")
        if reentry_plan is None:
            # 保留内部直接调用的最小入口；公开 Recovery protocol 会在调用方先解析
            # authority 并显式传入，Executor 本身不重新计算业务 target。
            reentry_plan = await FailureTargetResolver().resolve(
                workspace=workspace,
                source=source,
                graph=graph,
            )
        return await prepare_native_recovery(
            workspace=workspace,
            source_run_id=source_run_id,
            graph=graph,
            reentry_plan=reentry_plan,
        )

    async def prepare_interrupted_continue(
        self,
        *,
        workspace: str,
        source_run_id: str,
        graph: Any,
        reentry_plan: WorkflowReentryPlan | None = None,
    ) -> NativeRecoveryRuntimeContext:
        """解析最新 INTERRUPTED checkpoint 后复用统一 claim、handoff 与 fork transaction。"""

        source = await get_execution(workspace, source_run_id)
        if source is None:
            raise RecoveryExecutionError("SOURCE_EXECUTION_NOT_FOUND", "source execution 不存在。")
        if reentry_plan is None:
            resolution = await InterruptedTargetResolver().resolve(
                workspace=workspace,
                source=source,
                graph=graph,
            )
            if resolution.kind != "continue" or resolution.reentry_plan is None:
                raise RecoveryExecutionError(resolution.reason_code, resolution.reason)
            reentry_plan = resolution.reentry_plan
        if reentry_plan.reason is not WorkflowReentryReason.INTERRUPTED_CONTINUE:
            raise RecoveryExecutionError(
                "WORKFLOW_REENTRY_PLAN_INVALID",
                "INTERRUPTED continue 必须使用 INTERRUPTED_CONTINUE authority。",
            )
        return await prepare_native_recovery(
            workspace=workspace,
            source_run_id=source_run_id,
            graph=graph,
            reentry_plan=reentry_plan,
        )

    def materialize_revision_context(
        self,
        *,
        plan: WorkflowReentryPlan,
        semantic_state: dict[str, Any],
        child_run_id: str,
    ) -> dict[str, Any]:
        """验证 Revision authority 后只覆盖新的 execution metadata。"""

        if (
            plan.reason is not WorkflowReentryReason.REVISION
            or plan.context_authority.revision_context_sha256
            != semantic_context_sha256(semantic_state)
        ):
            raise RecoveryExecutionError(
                "REVISION_CONTEXT_AUTHORITY_INVALID",
                "Formal Revision Semantic Context 已偏离 Coordinator 确认的 authority。",
            )
        materialized = dict(semantic_state)
        materialized.update(
            {
                "active_run_id": child_run_id,
                "active_thread_id": plan.thread_id,
                "resume_from": "",
            }
        )
        return materialized


async def prepare_native_recovery(
    *,
    workspace: str,
    source_run_id: str,
    graph: Any,
    reentry_plan: WorkflowReentryPlan,
) -> NativeRecoveryRuntimeContext:
    """消费已解析的 Node Re-entry authority，并完成 claim、handoff、fork 和 STARTED。"""

    source = await get_execution(workspace, source_run_id)
    if source is None:
        raise RecoveryExecutionError(
            "SOURCE_EXECUTION_NOT_FOUND",
            "source execution 不存在。",
        )
    if source.status not in {
        DurableExecutionStatus.FAILED,
        DurableExecutionStatus.INTERRUPTED,
    }:
        raise RecoveryExecutionError(
            "WORKFLOW_REENTRY_PLAN_INVALID",
            "当前 source 不是 FAILED 或 INTERRUPTED，不能执行 checkpoint re-entry。",
        )
    state_reader = getattr(graph, "aget_state", None)
    if not callable(state_reader):
        raise RecoveryExecutionError(
            "RECOVERY_CHECKPOINT_RUN_INVALID",
            "当前 production Graph 无法读取最新 root checkpoint execution identity。",
        )
    try:
        latest_snapshot = await state_reader(
            {
                "configurable": {
                    "thread_id": source.thread_id,
                    "checkpoint_ns": "",
                }
            }
        )
    except Exception as exc:
        raise RecoveryExecutionError(
            "RECOVERY_CHECKPOINT_RUN_INVALID",
            "最新 root checkpoint execution identity 无法读取。",
        ) from exc
    latest_values = getattr(latest_snapshot, "values", {})
    latest_values = latest_values if isinstance(latest_values, dict) else {}
    checkpoint_run_id = str(latest_values.get("active_run_id") or "").strip()
    lineage = await resolve_recovery_lineage_head(
        workspace,
        thread_id=source.thread_id,
        execution_kind=source.execution_kind,
        authoritative_run_id=checkpoint_run_id,
    )
    if lineage.state is RecoveryLineageState.AMBIGUOUS:
        raise RecoveryExecutionError(
            lineage.reason_code,
            "Recovery lineage 存在多个无法安全解释的当前 head。",
        )
    if lineage.head is None:
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_NOT_CURRENT",
            "当前没有可用的 recovery source。",
        )
    if lineage.head.run_id != source.run_id:
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_SUPERSEDED",
            "当前 recovery source 已被新的 child execution 替代，请刷新后继续。",
            details={"currentSourceRunId": lineage.head.run_id},
        )
    if (
        reentry_plan.source_run_id != source.run_id
        or reentry_plan.thread_id != source.thread_id
        or reentry_plan.execution_kind != source.execution_kind
        or (
            source.status is DurableExecutionStatus.FAILED
            and reentry_plan.target_node != source.current_node
        )
    ):
        raise RecoveryExecutionError(
            "WORKFLOW_REENTRY_PLAN_INVALID",
            "WorkflowReentryPlan 与当前 source execution identity 不一致。",
        )
    if source.status is DurableExecutionStatus.FAILED:
        if reentry_plan.reason is not WorkflowReentryReason.FAILURE_RETRY:
            raise RecoveryExecutionError(
                "WORKFLOW_REENTRY_PLAN_INVALID",
                "FAILED source 只能使用 FAILURE_RETRY authority。",
            )
        fresh_reentry_plan = await FailureTargetResolver().resolve(
            workspace=workspace,
            source=source,
            graph=graph,
        )
    else:
        if reentry_plan.reason is not WorkflowReentryReason.INTERRUPTED_CONTINUE:
            raise RecoveryExecutionError(
                "WORKFLOW_REENTRY_PLAN_INVALID",
                "INTERRUPTED source 只能使用 INTERRUPTED_CONTINUE authority。",
            )
        resolution = await InterruptedTargetResolver().resolve(
            workspace=workspace,
            source=source,
            graph=graph,
        )
        if resolution.kind != "continue" or resolution.reentry_plan is None:
            raise RecoveryExecutionError(resolution.reason_code, resolution.reason)
        fresh_reentry_plan = resolution.reentry_plan
    _require_fresh_reentry_plan(
        expected=reentry_plan,
        fresh=fresh_reentry_plan,
    )
    # 这里只把已验证的 WorkflowReentryPlan 投影为现有 claim/fork transaction DTO。
    plan = recovery_plan_from_reentry(fresh_reentry_plan)
    plan = await _apply_application_planning_transaction_authority(
        workspace=workspace,
        source=source,
        graph=graph,
        plan=plan,
    )
    _require_checkpoint_reentry_plan(plan, source=source)
    identity = current_backend_instance()
    new_run_id = f"recovery-{uuid4().hex[:12]}"
    child_execution, _, attempt = await claim_native_recovery_attempt(
        source=source,
        plan=plan,
        new_run_id=new_run_id,
        owner_backend_instance_id=identity.instance_id,
        owner_pid=identity.pid,
        lease_ttl_seconds=Settings.from_env().execution_recovery_lease_ttl_seconds,
    )
    heartbeat_task = _start_recovery_heartbeat(
        workspace=workspace,
        run_id=new_run_id,
        owner_backend_instance_id=identity.instance_id,
    )
    handoff_completed = False
    try:
        lifecycle = _handoff_lifecycle(
            workspace,
            source=source,
            plan=plan,
            new_run_id=new_run_id,
        )
        handoff_completed = True
        await update_recovery_attempt(
            workspace=workspace,
            new_run_id=new_run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        await stop_execution_heartbeat(heartbeat_task)
        heartbeat_task = None
        return await finalize_handed_off_recovery_attempt(
            workspace=workspace,
            new_run_id=new_run_id,
            graph=graph,
        )
    except Exception as exc:
        await stop_execution_heartbeat(heartbeat_task)
        await _handle_pre_runtime_failure(
            workspace=workspace,
            new_run_id=new_run_id,
            attempt=await get_recovery_attempt(workspace, new_run_id),
            error_code=_recovery_error_code(exc),
            handoff_completed=handoff_completed,
        )
        if isinstance(exc, RecoveryExecutionError):
            raise
        raise RecoveryExecutionError(
            "RECOVERY_PRESTART_FAILED",
            "Native Recovery 在 Graph 启动前失败。",
        ) from exc


async def _apply_application_planning_transaction_authority(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    graph: Any,
    plan: RecoveryPlan,
) -> RecoveryPlan:
    """直接读取 TechnicalPlan INPUT_COMMITTED boundary，保留既有 lifecycle handoff 语义。"""

    if source.execution_kind != "application_planning":
        return plan
    state_reader = getattr(graph, "aget_state", None)
    if not callable(state_reader):
        return plan
    config = {
        "configurable": {
            "thread_id": source.thread_id,
            "checkpoint_ns": plan.checkpoint_ns,
            "checkpoint_id": plan.checkpoint_id,
        }
    }
    try:
        snapshot = await state_reader(config)
    except Exception as exc:
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_CHECKPOINT_INVALID",
            "TechnicalPlan transaction boundary 的 source checkpoint 无法读取。",
        ) from exc
    values = getattr(snapshot, "values", {})
    values = values if isinstance(values, dict) else {}
    boundary = parse_application_planning_boundary(
        values.get("application_planning_recovery_boundary")
    )
    if boundary is None or boundary.boundary is not ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED:
        return plan

    lifecycle = load_application_lifecycle(workspace)
    if lifecycle is None:
        raise RecoveryExecutionError(
            "LIFECYCLE_DRIFT",
            "TechnicalPlan INPUT_COMMITTED boundary 缺少 ApplicationLifecycle authority。",
        )
    request = _technical_request_from_values(values)
    if boundary.request_sha256 != application_planning_sha256(request):
        raise RecoveryExecutionError(
            "LIFECYCLE_DRIFT",
            "TechnicalPlan transaction boundary 的 request authority 已发生变化。",
        )
    if (
        boundary.operation
        in {
            ApplicationPlanningOperation.REVISE,
            ApplicationPlanningOperation.REPAIR,
        }
        and boundary.baseline_sha256 is None
    ):
        raise RecoveryExecutionError(
            "LIFECYCLE_DRIFT",
            "TechnicalPlan 修订或修复 boundary 缺少 baseline authority。",
        )

    change_id = str(values.get("change_id") or "").strip()
    if boundary.operation is ApplicationPlanningOperation.REVISE and change_id:
        _validate_formal_revision_transaction_identity(
            values=values,
            boundary=boundary,
            lifecycle=lifecycle,
        )

    stage = lifecycle.initialization.stage
    status = lifecycle.initialization.status
    mode = RecoveryLifecycleOwnershipMode.SOURCE_OWNED
    if stage is ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY:
        if (
            boundary.operation is not ApplicationPlanningOperation.INITIAL
            or status is not ApplicationLifecycleStatus.AWAITING_USER
        ):
            raise RecoveryExecutionError(
                "LIFECYCLE_DRIFT",
                "TechnicalPlan initial INPUT_COMMITTED boundary 不在允许的 lifecycle 窗口。",
            )
        mode = RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP
    elif stage is ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION:
        if (
            boundary.operation
            not in {
                ApplicationPlanningOperation.REVISE,
                ApplicationPlanningOperation.REPAIR,
            }
            or status is not ApplicationLifecycleStatus.AWAITING_USER
        ):
            raise RecoveryExecutionError(
                "LIFECYCLE_DRIFT",
                "TechnicalPlan 修订 INPUT_COMMITTED boundary 不在允许的 lifecycle 窗口。",
            )
        mode = RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP
    elif (
        boundary.operation is ApplicationPlanningOperation.REVISE
        and lifecycle.initialization.status is ApplicationLifecycleStatus.COMPLETED
        and stage is ApplicationLifecycleStage.READY_FOR_WORKBENCH
        and (
            lifecycle.pending_revision_impact is not None
            or lifecycle.active_formal_revision is not None
        )
    ):
        mode = RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP
    elif not (
        stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN
        and status is ApplicationLifecycleStatus.RUNNING
        and lifecycle.active_run_id == source.run_id
    ):
        raise RecoveryExecutionError(
            "LIFECYCLE_DRIFT",
            "TechnicalPlan INPUT_COMMITTED boundary 的 lifecycle ownership 已发生变化。",
        )
    return plan.model_copy(update={"lifecycle_ownership_mode": mode})


def _validate_formal_revision_transaction_identity(
    *,
    values: dict[str, Any],
    boundary: Any,
    lifecycle: Any,
) -> None:
    """验证 Formal Revision transaction 的 change、gate、请求、分支和目标事实一致。"""

    change_id = str(values.get("change_id") or "").strip()
    target_value = values.get("change_target")
    if not change_id or not isinstance(target_value, dict):
        raise RecoveryExecutionError(
            "LIFECYCLE_DRIFT",
            "Formal Revision INPUT_COMMITTED boundary 缺少 change identity。",
        )
    try:
        target = RevisionTarget.model_validate(target_value)
    except Exception as exc:
        raise RecoveryExecutionError(
            "LIFECYCLE_DRIFT",
            "Formal Revision INPUT_COMMITTED boundary 的 target identity 无效。",
        ) from exc
    request = _technical_request_from_values(values)
    pending = lifecycle.pending_revision_impact
    active = lifecycle.active_formal_revision
    if pending is not None and active is not None:
        raise RecoveryExecutionError(
            "LIFECYCLE_DRIFT",
            "Formal Revision lifecycle 同时存在 pending 与 active authority。",
        )
    if pending is not None:
        matches = (
            pending.change_id == change_id
            and pending.interaction_id == str(boundary.gate_id or "")
            and pending.request == request
            and pending.impact.formal_branch.value == "workbench_plan_revision"
            and pending.target == target
        )
    elif active is not None:
        matches = (
            active.change_id == change_id
            and active.impact_interaction_id == str(boundary.gate_id or "")
            and active.request == request
            and active.formal_branch.value == "workbench_plan_revision"
            and active.target == target
        )
    else:
        matches = False
    if not matches:
        raise RecoveryExecutionError(
            "LIFECYCLE_DRIFT",
            "Formal Revision INPUT_COMMITTED boundary 的 lifecycle authority 不匹配。",
        )


def _technical_request_from_values(values: dict[str, Any]) -> str:
    """按 Graph State 当前请求优先级读取 TechnicalPlan transaction 的输入原文。"""

    interaction = values.get("application_planning_interaction")
    if isinstance(interaction, dict) and interaction:
        return str(interaction.get("request") or "").strip()
    return str(
        values.get("design_change_generation_request")
        or values.get("design_change_request")
        or values.get("request")
        or ""
    ).strip()


async def finalize_handed_off_recovery_attempt(
    *,
    workspace: str,
    new_run_id: str,
    graph: Any,
) -> Any:
    """独占 finalization 后重验恢复世界，再创建 durable fork checkpoint。"""

    attempt = await get_recovery_attempt(workspace, new_run_id)
    if attempt is None or attempt.status not in {
        RecoveryAttemptStatus.HANDED_OFF,
        RecoveryAttemptStatus.FINALIZING,
    }:
        return attempt
    identity = current_backend_instance()
    attempt, _lease = await claim_recovery_finalization(
        workspace=workspace,
        new_run_id=new_run_id,
        new_owner_backend_instance_id=identity.instance_id,
        new_owner_pid=identity.pid,
        lease_ttl_seconds=Settings.from_env().execution_recovery_lease_ttl_seconds,
    )

    # FINALIZING claim 成功后立即续租，确保后续所有重验证和 fork 操作都保持独占权。
    heartbeat_task = _start_recovery_heartbeat(
        workspace=workspace,
        run_id=new_run_id,
        owner_backend_instance_id=identity.instance_id,
    )
    try:
        source = await get_execution(workspace, attempt.source_run_id)
        child_execution = await get_execution(workspace, new_run_id)
        if source is None:
            raise RecoveryExecutionError(
                "SOURCE_EXECUTION_NOT_FOUND",
                "RecoveryAttempt 的 source execution 不存在。",
            )
        if child_execution is None:
            raise RecoveryExecutionError(
                "RECOVERY_EXECUTION_NOT_FOUND",
                "FINALIZING recovery 的 child execution 不存在。",
            )
        if source.status not in {
            DurableExecutionStatus.FAILED,
            DurableExecutionStatus.INTERRUPTED,
        }:
            raise RecoveryExecutionError(
                "WORKFLOW_REENTRY_PLAN_INVALID",
                "当前 source 不是 FAILED 或 INTERRUPTED，不能执行 checkpoint re-entry。",
            )
        lifecycle = load_application_lifecycle(workspace)
        plan = RecoveryPlan(
            source_run_id=source.run_id,
            thread_id=source.thread_id,
            target_node=child_execution.first_node,
            checkpoint_id=attempt.source_checkpoint_id,
            checkpoint_ns=attempt.source_checkpoint_ns,
            lifecycle_ownership_mode=attempt.lifecycle_ownership_mode,
            lifecycle_revision=attempt.source_lifecycle_revision,
        )
        _require_checkpoint_reentry_plan(plan, source=source)
        source_snapshot = await _revalidate_finalizing_recovery(
            workspace=workspace,
            source=source,
            child_execution=child_execution,
            attempt=attempt,
            graph=graph,
            lifecycle=lifecycle,
        )
        return await _fork_and_start(
            workspace=workspace,
            source=source,
            plan=plan,
            source_snapshot=source_snapshot,
            new_run_id=new_run_id,
            graph=graph,
            lifecycle=lifecycle,
            attempt=attempt,
            child_execution=child_execution,
            heartbeat_task=heartbeat_task,
        )
    except Exception as exc:
        await stop_execution_heartbeat(heartbeat_task)
        await _handle_finalization_failure(
            workspace=workspace,
            new_run_id=new_run_id,
            error_code=_recovery_error_code(exc),
        )
        raise


async def _fork_and_start(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    plan: RecoveryPlan,
    source_snapshot: Any,
    new_run_id: str,
    graph: Any,
    lifecycle: Any,
    attempt: RecoveryAttempt,
    child_execution: DurableExecutionRecord,
    heartbeat_task: asyncio.Task[None] | None,
) -> NativeRecoveryRuntimeContext:
    """只写 runtime identity 的 fork checkpoint，并把 cache 写入降级为 best-effort。"""

    if source.status not in {
        DurableExecutionStatus.FAILED,
        DurableExecutionStatus.INTERRUPTED,
    }:
        raise RecoveryExecutionError(
            "WORKFLOW_REENTRY_PLAN_INVALID",
            "当前 source 不是 FAILED 或 INTERRUPTED，不能执行 checkpoint re-entry。",
        )
    _require_checkpoint_reentry_plan(plan, source=source)
    _validate_checkpoint_reentry_source(
        source=source,
        attempt=attempt,
        plan=plan,
        target_node=child_execution.first_node,
    )
    if not hasattr(graph, "aupdate_state") or not hasattr(graph, "aget_state"):
        raise RecoveryExecutionError(
            "RECOVERY_FORK_UNSUPPORTED",
            "当前 Graph 不支持 Native Recovery state fork。",
        )
    source_config = {
        "configurable": {
            "thread_id": source.thread_id,
            "checkpoint_ns": plan.checkpoint_ns,
            "checkpoint_id": plan.checkpoint_id,
        }
    }
    source_thread_id, source_ns, source_checkpoint_id = _snapshot_identity(source_snapshot)
    if (
        source_thread_id != source.thread_id
        or source_ns != plan.checkpoint_ns
        or source_checkpoint_id != plan.checkpoint_id
    ):
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_CHECKPOINT_INVALID",
            "source Node Entry checkpoint identity 已偏离 RecoveryAttempt。",
        )
    source_next = [str(node) for node in (getattr(source_snapshot, "next", ()) or ())]
    if source_next != [plan.target_node]:
        raise RecoveryExecutionError(
            "RECOVERY_FORK_CONTROL_FLOW_DRIFT",
            "source Node Entry checkpoint 的 nextNodes 已偏离 Recovery authority。",
        )
    source_values = getattr(source_snapshot, "values", {})
    source_values = source_values if isinstance(source_values, dict) else {}
    if str(source_values.get("active_run_id") or "") != source.run_id:
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_CHECKPOINT_INVALID",
            "source Node Entry checkpoint 不属于当前 source run。",
        )
    observability = _recovery_observability(
        run_id=new_run_id,
        thread_id=source.thread_id,
        project_id=source.project_id,
        workspace=workspace,
    )
    updates: dict[str, Any] = {
        "active_run_id": new_run_id,
        "active_thread_id": source.thread_id,
        "observability": observability,
    }
    if lifecycle is not None:
        updates["lifecycle"] = application_lifecycle_payload(lifecycle)
    try:
        fork_config = await graph.aupdate_state(source_config, updates)
    except Exception as exc:
        if _is_ambiguous_fork_error(exc):
            raise RecoveryExecutionError(
                "RECOVERY_FORK_AMBIGUOUS",
                "LangGraph 无法确定 Native Recovery fork 的 writer。",
            ) from exc
        raise RecoveryExecutionError(
            "RECOVERY_FORK_FAILED",
            "Native Recovery 无法创建 fork checkpoint。",
        ) from exc
    if not isinstance(fork_config, dict):
        raise RecoveryExecutionError(
            "RECOVERY_FORK_NOT_CREATED",
            "LangGraph 没有返回 fork checkpoint config。",
        )
    fork_snapshot = await graph.aget_state(fork_config)
    fork_thread_id, fork_ns, fork_checkpoint_id = _snapshot_identity(fork_snapshot)
    if fork_thread_id != source.thread_id or fork_ns != "":
        raise RecoveryExecutionError(
            "RECOVERY_FORK_NOT_CREATED",
            "Native Recovery 只支持 root checkpoint fork。",
        )
    if fork_checkpoint_id == plan.checkpoint_id:
        raise RecoveryExecutionError(
            "RECOVERY_FORK_NOT_CREATED",
            "aupdate_state 没有产生新的 fork checkpoint。",
        )
    fork_next = [str(node) for node in (getattr(fork_snapshot, "next", ()) or ())]
    if fork_next != source_next:
        raise RecoveryExecutionError(
            "RECOVERY_FORK_CONTROL_FLOW_DRIFT",
            "fork identity overlay 改变了 source Node Entry checkpoint 的 nextNodes。",
        )
    values = getattr(fork_snapshot, "values", {})
    values = values if isinstance(values, dict) else {}
    if str(values.get("active_run_id") or "") != new_run_id:
        raise RecoveryExecutionError(
            "RECOVERY_FORK_IDENTITY_MISMATCH",
            "fork checkpoint 未写入 child active_run_id。",
        )
    await update_recovery_attempt(
        workspace=workspace,
        new_run_id=new_run_id,
        status=RecoveryAttemptStatus.STARTED,
    )
    await _persist_child_boundary_index(
        workspace=workspace,
        run_id=new_run_id,
        thread_id=source.thread_id,
        target_node=plan.target_node,
        checkpoint_id=fork_checkpoint_id,
    )
    workspace_lease = _acquire_workspace_lease(
        workspace=workspace,
        source=source,
        new_run_id=new_run_id,
        lifecycle=lifecycle,
    )
    replay_config = dict(fork_config)
    replay_config.setdefault("configurable", {})
    replay_config["configurable"] = {
        **replay_config["configurable"],
        "thread_id": source.thread_id,
        "checkpoint_ns": "",
        "checkpoint_id": fork_checkpoint_id,
    }
    observation_config = {
        "configurable": {
            "thread_id": source.thread_id,
            "checkpoint_ns": "",
        }
    }
    return NativeRecoveryRuntimeContext(
        source_execution=source,
        child_execution=child_execution,
        recovery_plan=plan,
        new_run_id=new_run_id,
        thread_id=source.thread_id,
        project_id=source.project_id,
        workspace=workspace,
        workflow_scope=source.workflow_scope,
        graph=graph,
        fork_config=replay_config,
        observation_config=observation_config,
        fork_snapshot=fork_snapshot,
        lifecycle_payload=(
            application_lifecycle_payload(lifecycle) if lifecycle is not None else None
        ),
        workspace_lease=workspace_lease,
        observability=observability,
        heartbeat_task=heartbeat_task,
    )


async def _persist_child_boundary_index(
    *,
    workspace: str,
    run_id: str,
    thread_id: str,
    target_node: str,
    checkpoint_id: str,
) -> None:
    """best-effort 索引 child fork，不让 cache 故障触发第二次 Graph fork。"""

    try:
        await insert_node_entry_boundary(
            workspace=workspace,
            boundary=NodeEntryBoundary(
                boundary_id=f"node-entry-{uuid4().hex}",
                source_run_id=run_id,
                thread_id=thread_id,
                target_node=target_node,
                checkpoint_id=checkpoint_id,
                checkpoint_ns="",
                captured_at=datetime.now(timezone.utc),
            ),
        )
    except Exception:
        return


async def _revalidate_finalizing_recovery(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    child_execution: DurableExecutionRecord,
    attempt: RecoveryAttempt,
    graph: Any,
    lifecycle: Any,
) -> Any:
    """在唯一 finalizer 持有 fork 权后重新证明 checkpoint、磁盘和 ownership。"""

    admission = assess_recovery_source(source)
    if not admission.admissible:
        raise RecoveryExecutionError(
            "RECOVERY_STATE_DRIFT",
            "source execution 不再满足 recovery source admission，不能继续 finalization。",
        )
    if source.status is not attempt.source_status:
        raise RecoveryExecutionError(
            "RECOVERY_STATE_DRIFT",
            "source execution 的终止状态已偏离 RecoveryAttempt。",
        )
    if execution_failure_sha256(source.failure) != attempt.source_failure_sha256:
        raise RecoveryExecutionError(
            "RECOVERY_STATE_DRIFT",
            "source execution 的 failure evidence 已偏离 RecoveryAttempt。",
        )
    if (
        source.thread_id != attempt.thread_id
        or not attempt.source_checkpoint_id
        or attempt.source_checkpoint_ns != ""
        or child_execution.thread_id != source.thread_id
        or child_execution.first_node.strip() == ""
    ):
        raise RecoveryExecutionError(
            "RECOVERY_STATE_DRIFT",
            "Native Recovery 的 source checkpoint authority 已发生变化。",
        )
    _validate_checkpoint_reentry_source(
        source=source,
        attempt=attempt,
        target_node=child_execution.first_node,
    )
    if not hasattr(graph, "aget_state"):
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_CHECKPOINT_INVALID",
            "当前 Graph 不支持重新读取 source checkpoint。",
        )
    source_config = {
        "configurable": {
            "thread_id": source.thread_id,
            "checkpoint_ns": attempt.source_checkpoint_ns,
            "checkpoint_id": attempt.source_checkpoint_id,
        }
    }
    try:
        snapshot = await graph.aget_state(source_config)
        thread_id, checkpoint_ns, checkpoint_id = _snapshot_identity(snapshot)
    except Exception as exc:
        if isinstance(exc, RecoveryExecutionError):
            raise RecoveryExecutionError(
                "RECOVERY_SOURCE_CHECKPOINT_INVALID",
                "source checkpoint 无法重新读取或身份不完整。",
            ) from exc
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_CHECKPOINT_INVALID",
            "source checkpoint 无法重新读取。",
        ) from exc
    if (
        thread_id != source.thread_id
        or checkpoint_ns != attempt.source_checkpoint_ns
        or checkpoint_id != attempt.source_checkpoint_id
    ):
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_CHECKPOINT_INVALID",
            "source checkpoint identity 已偏离 RecoveryAttempt。",
        )
    values = getattr(snapshot, "values", {})
    values = values if isinstance(values, dict) else {}
    if str(values.get("active_run_id") or "") != source.run_id:
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_CHECKPOINT_INVALID",
            "source checkpoint 不属于当前 source run。",
        )
    next_nodes = [str(node) for node in (getattr(snapshot, "next", ()) or ())]
    if next_nodes != [child_execution.first_node]:
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_CHECKPOINT_INVALID",
            "source checkpoint nextNodes 已偏离 child first Node。",
        )
    if source.status is DurableExecutionStatus.FAILED and next_nodes != [source.current_node]:
        raise RecoveryExecutionError(
            "RECOVERY_STATE_DRIFT",
            "FAILED source 的 checkpoint successor 已偏离当前失败节点。",
        )
    if any(getattr(task, "interrupts", ()) for task in getattr(snapshot, "tasks", ()) or ()):
        raise RecoveryExecutionError(
            "RECOVERY_SOURCE_CHECKPOINT_INVALID",
            "source checkpoint 仍在等待交互，不能走 Native Recovery finalization。",
        )

    if source.status is DurableExecutionStatus.FAILED:
        fresh_plan = await FailureTargetResolver().resolve(
            workspace=workspace,
            source=source,
            graph=graph,
        )
        fresh_authority = fresh_plan.context_authority
        if (
            fresh_plan.target_node != child_execution.first_node
            or fresh_authority.checkpoint_id != attempt.source_checkpoint_id
            or fresh_authority.checkpoint_ns != attempt.source_checkpoint_ns
        ):
            raise RecoveryExecutionError(
                "RECOVERY_STATE_DRIFT",
                "FAILED source 的最新 checkpoint authority 已发生变化。",
            )
    else:
        resolution = await InterruptedTargetResolver().resolve(
            workspace=workspace,
            source=source,
            graph=graph,
            require_current_lineage=False,
        )
        fresh_plan = resolution.reentry_plan
        fresh_authority = fresh_plan.context_authority if fresh_plan is not None else None
        if (
            resolution.kind != "continue"
            or fresh_plan is None
            or fresh_plan.target_node != child_execution.first_node
            or fresh_authority is None
            or fresh_authority.checkpoint_id != attempt.source_checkpoint_id
            or fresh_authority.checkpoint_ns != attempt.source_checkpoint_ns
        ):
            raise RecoveryExecutionError(
                "RECOVERY_STATE_DRIFT",
                "INTERRUPTED source 的最新 checkpoint authority 已发生变化。",
            )

    _validate_recovery_workspace_authority(
        workspace=workspace,
        values=values,
    )
    _validate_finalization_lifecycle(
        source=source,
        attempt=attempt,
        lifecycle=lifecycle,
    )
    return snapshot


def _validate_recovery_workspace_authority(
    *,
    workspace: str,
    values: dict[str, Any],
) -> None:
    """在 finalization 内直接验证 LangGraph State 绑定的 workspace authority。"""

    workspace_revision = _optional_text(values.get("workspace_revision"))
    workspace_snapshot_hash = _optional_text(values.get("workspace_snapshot_hash"))
    if workspace_revision is None and workspace_snapshot_hash is None:
        return
    if workspace_revision is None:
        raise RecoveryExecutionError(
            "WORKSPACE_STATE_UNVERIFIABLE",
            "LangGraph checkpoint 缺少 workspaceRevision，无法验证当前磁盘状态。",
        )

    workspace_root = Path(workspace).expanduser().resolve()
    if not workspace_root.is_dir():
        raise RecoveryExecutionError(
            "WORKSPACE_STATE_UNVERIFIABLE",
            "当前工作区不存在，无法安全读取真实磁盘状态。",
        )
    try:
        _files, current_revision = workspace_inventory(workspace_root)
    except (OSError, ValueError) as exc:
        raise RecoveryExecutionError(
            "WORKSPACE_STATE_UNVERIFIABLE",
            "当前工作区状态无法安全读取。",
        ) from exc
    if not current_revision:
        raise RecoveryExecutionError(
            "WORKSPACE_STATE_UNVERIFIABLE",
            "当前工作区未能生成有效 revision，无法安全验证恢复现场。",
        )
    if current_revision != workspace_revision:
        raise RecoveryExecutionError(
            "WORKSPACE_DRIFT",
            "当前 workspace revision 已偏离 LangGraph checkpoint。",
        )
    if workspace_snapshot_hash is None:
        return

    snapshot = _load_workspace_snapshot_for_revision(workspace, current_revision)
    if snapshot is None:
        raise RecoveryExecutionError(
            "WORKSPACE_SNAPSHOT_UNAVAILABLE",
            "当前 workspace revision 缺少可验证的 snapshot 证据。",
        )
    if snapshot_hash(snapshot) != workspace_snapshot_hash:
        raise RecoveryExecutionError(
            "WORKSPACE_DRIFT",
            "当前 workspace snapshot hash 已偏离 LangGraph checkpoint。",
        )


def _load_workspace_snapshot_for_revision(
    workspace: str,
    revision: str,
) -> dict[str, Any] | None:
    """按精确 revision 和当前 inspector schema 读取已有 workspace snapshot。"""

    roots = (
        Path(workspace).expanduser().resolve()
        / ".xcodeagent"
        / "cache"
        / "workspace-snapshots",
        Path(workspace).expanduser().resolve() / "cache" / "workspace-snapshots",
    )
    for root in roots:
        path = root / f"{revision}.{INSPECTOR_SCHEMA_VERSION}.json"
        if not path.is_file():
            continue
        try:
            snapshot = load_workspace_snapshot_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(snapshot, dict):
            return snapshot
    return None


def _optional_text(value: Any) -> str | None:
    """把 checkpoint 中的可选 authority 字段规范化为非空文本。"""

    normalized = str(value or "").strip()
    return normalized or None


def _validate_checkpoint_reentry_source(
    *,
    source: DurableExecutionRecord,
    attempt: RecoveryAttempt,
    target_node: str,
    plan: RecoveryPlan | None = None,
) -> None:
    """在 Native fork 前重新证明 FAILED 或 INTERRUPTED source 的 checkpoint authority。"""

    if (
        source.status
        not in {
            DurableExecutionStatus.FAILED,
            DurableExecutionStatus.INTERRUPTED,
        }
        or source.thread_id != attempt.thread_id
        or not attempt.source_checkpoint_id
        or attempt.source_checkpoint_ns != ""
        or not target_node
        or (plan is not None and plan.checkpoint_id != attempt.source_checkpoint_id)
        or (plan is not None and plan.checkpoint_ns != attempt.source_checkpoint_ns)
        or (plan is not None and plan.target_node != target_node)
        or (
            source.status is DurableExecutionStatus.FAILED
            and target_node != source.current_node
        )
    ):
        raise RecoveryExecutionError(
            "RECOVERY_STATE_DRIFT",
            "source checkpoint identity 已偏离当前节点或 RecoveryAttempt authority。",
        )


def _validate_finalization_lifecycle(
    *,
    source: DurableExecutionRecord,
    attempt: RecoveryAttempt,
    lifecycle: Any,
) -> None:
    """验证 handoff 后 child 仍拥有正确的生命周期和资源锁。"""

    if lifecycle is None:
        raise RecoveryExecutionError(
            "RECOVERY_STATE_DRIFT",
            (
                "Application Planning Recovery finalization 缺少 ApplicationLifecycle，"
                "无法证明 child ownership。"
                if source.execution_kind == "application_planning"
                else "Workbench finalization 缺少 ApplicationLifecycle。"
            ),
        )
    if source.execution_kind == "application_planning":
        if (
            lifecycle.active_run_id != attempt.new_run_id
            or lifecycle.initialization.thread_id != attempt.thread_id
        ):
            raise RecoveryExecutionError(
                "RECOVERY_STATE_DRIFT",
                "Application Planning lifecycle 不再属于 child execution。",
            )
        return

    execution = lifecycle.active_executions.get(attempt.new_run_id)
    if execution is None:
        raise RecoveryExecutionError(
            "RECOVERY_STATE_DRIFT",
            "Workbench lifecycle 中找不到 child execution。",
        )
    if (
        lifecycle.active_run_id != attempt.new_run_id
        or execution.thread_id != attempt.thread_id
        or execution.status.value != "running"
        or execution.pending_interaction is not None
    ):
        raise RecoveryExecutionError(
            "RECOVERY_STATE_DRIFT",
            "Workbench child execution 的 ownership 或运行状态已变化。",
        )
    claim_keys = {
        f"{claim.type.value}:{claim.target_id}"
        for claim in resource_claims_for_run(lifecycle.resource_locks, attempt.new_run_id)
    }
    if claim_keys != set(execution.resource_keys):
        raise RecoveryExecutionError(
            "RECOVERY_STATE_DRIFT",
            "Workbench child execution 的 resource locks 已发生 owner drift。",
        )


def _handoff_lifecycle(
    workspace: str,
    *,
    source: DurableExecutionRecord,
    plan: RecoveryPlan,
    new_run_id: str,
) -> Any:
    """按 execution kind 选择 Workbench 或 Application Planning 的 handoff。"""

    if source.execution_kind == "application_planning":
        if plan.lifecycle_revision is None:
            raise RecoveryExecutionError(
                "RECOVERY_LIFECYCLE_REVISION_MISSING",
                "Native Application Planning Recovery 缺少 Lifecycle revision。",
            )
        if plan.lifecycle_ownership_mode is RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP:
            return claim_application_planning_run_for_recovery(
                workspace,
                new_run_id=new_run_id,
                thread_id=source.thread_id,
                expected_lifecycle_revision=plan.lifecycle_revision,
            )
        return handoff_application_planning_run_for_recovery(
            workspace,
            source_run_id=source.run_id,
            new_run_id=new_run_id,
            thread_id=source.thread_id,
            expected_lifecycle_revision=plan.lifecycle_revision,
        )
    return handoff_workbench_execution_for_recovery(
        workspace,
        source_run_id=source.run_id,
        new_run_id=new_run_id,
        thread_id=source.thread_id,
        phase=plan.target_node,
        expected_lifecycle_revision=plan.lifecycle_revision,
    )


def _acquire_workspace_lease(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    new_run_id: str,
    lifecycle: Any,
) -> WorkspaceRunLease | None:
    """用 handoff 后的完整 resource lock 集合重建进程内 WorkspaceRunLease。"""

    if source.execution_kind != "workbench" or lifecycle is None:
        return None
    execution = lifecycle.active_executions.get(new_run_id)
    if execution is None:
        raise RecoveryExecutionError(
            "RECOVERY_STATE_DRIFT",
            "handoff 后找不到 child Workbench execution。",
        )
    claims = resource_claims_for_run(lifecycle.resource_locks, new_run_id)
    return workspace_run_leases.acquire(
        workspace_root=workspace,
        project_id=source.project_id,
        execution_scope={"type": execution.scope, "targetId": execution.target_id},
        resource_claims=[claim.model_dump(mode="json", by_alias=True) for claim in claims],
        thread_id=source.thread_id,
        run_id=new_run_id,
    )


def _require_checkpoint_reentry_plan(
    plan: RecoveryPlan,
    *,
    source: DurableExecutionRecord,
) -> None:
    """直接验证 FAILED 或 INTERRUPTED 的 checkpoint transaction bridge。"""

    if (
        not plan.checkpoint_id
        or plan.checkpoint_ns != ""
        or not plan.target_node
        or (
            source.status is DurableExecutionStatus.FAILED
            and plan.target_node != source.current_node
        )
    ):
        raise RecoveryExecutionError(
            "WORKFLOW_REENTRY_PLAN_INVALID",
            "Node Re-entry 缺少 root checkpoint transaction authority。",
        )


def _require_fresh_reentry_plan(
    *,
    expected: WorkflowReentryPlan,
    fresh: WorkflowReentryPlan,
) -> None:
    """比较 claim 前重新解析的完整 Node Entry authority，拒绝 stale plan。"""

    expected_authority = expected.context_authority
    fresh_authority = fresh.context_authority
    if (
        expected.source_run_id != fresh.source_run_id
        or expected.thread_id != fresh.thread_id
        or expected.target_node != fresh.target_node
        or expected.execution_kind != fresh.execution_kind
        or expected_authority.source_run_id != fresh_authority.source_run_id
        or expected_authority.thread_id != fresh_authority.thread_id
        or expected_authority.target_node != fresh_authority.target_node
        or expected_authority.checkpoint_id != fresh_authority.checkpoint_id
        or expected_authority.checkpoint_ns != fresh_authority.checkpoint_ns
        or expected.lifecycle_authority != fresh.lifecycle_authority
    ):
        raise RecoveryExecutionError(
            "WORKFLOW_REENTRY_PLAN_STALE",
            "重新验证后的 Workflow Node Entry authority 已偏离原始计划。",
        )


def _snapshot_identity(snapshot: Any) -> tuple[str, str, str]:
    """读取 fork StateSnapshot 的 thread、namespace 和 checkpoint 身份。"""

    config = getattr(snapshot, "config", {})
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    if not isinstance(configurable, dict):
        raise RecoveryExecutionError(
            "RECOVERY_FORK_NOT_CREATED",
            "fork StateSnapshot 缺少 configurable identity。",
        )
    thread_id = str(configurable.get("thread_id") or "")
    checkpoint_ns = str(configurable.get("checkpoint_ns") or "")
    checkpoint_id = str(configurable.get("checkpoint_id") or "")
    if not thread_id or not checkpoint_id:
        raise RecoveryExecutionError(
            "RECOVERY_FORK_NOT_CREATED",
            "fork StateSnapshot 缺少完整 checkpoint identity。",
        )
    return thread_id, checkpoint_ns, checkpoint_id


def _is_ambiguous_fork_error(error: Exception) -> bool:
    """识别 LangGraph 无法推断 writer 的错误而不猜测 as_node。"""

    return "InvalidUpdateError" in type(error).__name__ or "writer" in str(error).lower()


def _recovery_error_code(error: Exception) -> str:
    """从领域异常或 lifecycle 冲突消息提取稳定的恢复错误码。"""

    explicit = getattr(error, "code", None)
    if explicit:
        return str(explicit)
    message = str(error)
    for code in (
        "RECOVERY_STATE_DRIFT",
        "RECOVERY_REQUIRES_HANDLER",
        "LIFECYCLE_DRIFT",
    ):
        if code in message:
            return code
    return "RECOVERY_PRESTART_FAILED"


async def _handle_pre_runtime_failure(
    *,
    workspace: str,
    new_run_id: str,
    attempt: RecoveryAttempt | None,
    error_code: str,
    handoff_completed: bool = False,
) -> None:
    """在 Graph 尚未交给 Runtime 前按当前 durable 阶段安全收口 child。"""

    if attempt is None:
        return
    if attempt.status is RecoveryAttemptStatus.PREPARING and not handoff_completed:
        await fail_recovery_attempt_prestart(
            workspace=workspace,
            new_run_id=new_run_id,
            failure_code=str(error_code or "RECOVERY_PRESTART_FAILED"),
        )
        return
    if attempt.status is RecoveryAttemptStatus.PREPARING and handoff_completed:
        await update_recovery_attempt(
            workspace=workspace,
            new_run_id=new_run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
            failure_code=error_code,
        )
        return


async def _handle_finalization_failure(
    *,
    workspace: str,
    new_run_id: str,
    error_code: str,
) -> None:
    """把不可安全 fork 的 child 固化为 FINALIZATION_FAILED 并释放 lease。"""

    attempt = await get_recovery_attempt(workspace, new_run_id)
    if attempt is None:
        return
    if attempt.status is RecoveryAttemptStatus.FINALIZING:
        await update_recovery_attempt(
            workspace=workspace,
            new_run_id=new_run_id,
            status=RecoveryAttemptStatus.FINALIZATION_FAILED,
            failure_code=error_code,
        )
    elif attempt.status is not RecoveryAttemptStatus.FINALIZATION_FAILED:
        return
    lease = await get_execution_lease(workspace, new_run_id)
    execution = await get_execution(workspace, new_run_id)
    if (
        lease is not None
        and execution is not None
        and execution.status is DurableExecutionStatus.RUNNING
    ):
        await finish_execution_and_release_lease(
            workspace=workspace,
            run_id=new_run_id,
            status=DurableExecutionStatus.INTERRUPTED,
            owner_backend_instance_id=lease.owner_backend_instance_id,
            ended_at=datetime.now(timezone.utc),
        )


def _recovery_observability(
    *,
    run_id: str,
    thread_id: str,
    project_id: str | None,
    workspace: str,
) -> dict[str, Any]:
    """为 child run 生成不包含 checkpoint state 的观测身份。"""

    settings = Settings.from_env()
    return {
        "langsmith": {
            "enabled": settings.langsmith_tracing_enabled,
            "project": settings.langsmith_project,
            "endpoint": settings.langsmith_endpoint,
            "runId": run_id,
            "threadId": thread_id,
            "projectId": project_id,
            "workspace": workspace,
        }
    }


def _start_recovery_heartbeat(
    *,
    workspace: str,
    run_id: str,
    owner_backend_instance_id: str,
) -> asyncio.Task[None]:
    """在 claim 完成后立即续租，覆盖 handoff、fork 到 Runtime 接管前的窗口。"""

    settings = Settings.from_env()
    return asyncio.create_task(
        maintain_execution_heartbeat(
            workspace=workspace,
            run_id=run_id,
            backend_instance_id=owner_backend_instance_id,
            interval_seconds=settings.execution_recovery_heartbeat_seconds,
            lease_ttl_seconds=settings.execution_recovery_lease_ttl_seconds,
        )
    )


__all__ = [
    "NativeRecoveryRuntimeContext",
    "finalize_handed_off_recovery_attempt",
    "prepare_native_recovery",
]
