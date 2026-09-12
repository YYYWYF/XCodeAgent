"""P0.3B Native Recovery 的 claim、lifecycle handoff 与 checkpoint fork。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import uuid4

from app.config import Settings
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryAttempt,
    RecoveryAttemptStatus,
    RecoveryExecutionError,
    RecoveryPlan,
    RecoveryPoint,
    RecoveryPointKind,
    RecoveryStrategy,
)
from app.persistence.execution_recovery import (
    claim_native_recovery_attempt,
    finish_execution_and_release_lease,
    get_execution,
    get_execution_lease,
    get_recovery_attempt,
    get_recovery_point,
    fail_recovery_attempt_prestart,
    insert_recovery_point,
    takeover_pre_runtime_recovery_lease,
    update_recovery_attempt,
)
from app.services.application_lifecycle import (
    application_lifecycle_payload,
    handoff_application_planning_run_for_recovery,
    handoff_workbench_execution_for_recovery,
    load_application_lifecycle,
    resource_claims_for_run,
)
from app.services.backend_instance import current_backend_instance
from app.services.execution_recovery_coordinator import prepare_continue
from app.services.execution_lease_heartbeat import (
    maintain_execution_heartbeat,
    stop_execution_heartbeat,
)
from app.workspace.run_lease import WorkspaceRunLease, workspace_run_leases


@dataclass(slots=True)
class NativeRecoveryRuntimeContext:
    """把恢复准备事实传给现有 Runtime stream，而不是伪装成普通用户请求。"""

    source_execution: DurableExecutionRecord
    child_execution: DurableExecutionRecord
    recovery_plan: RecoveryPlan
    source_recovery_point: RecoveryPoint
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


async def prepare_native_recovery(
    *,
    workspace: str,
    source_run_id: str,
    graph: Any,
    replay_policies: Sequence[Any] | None = None,
) -> NativeRecoveryRuntimeContext:
    """重新准备 P0.3A plan，并按规定顺序完成 claim、handoff、fork 和 STARTED。"""

    source = await get_execution(workspace, source_run_id)
    if source is None:
        raise RecoveryExecutionError(
            "SOURCE_EXECUTION_NOT_FOUND",
            "source execution 不存在。",
        )
    plan = await prepare_continue(
        workspace=workspace,
        source_run_id=source_run_id,
        graph=graph,
        replay_policies=replay_policies,
    )
    _require_native_plan(plan)
    source_point = await get_recovery_point(workspace, plan.recovery_point_id or "")
    if source_point is None:
        raise RecoveryExecutionError(
            "INVALID_RECOVERY_POINT",
            "RecoveryPlan 引用的 RecoveryPoint 已不存在。",
        )
    identity = current_backend_instance()
    new_run_id = f"recovery-{uuid4().hex[:12]}"
    _validate_root_plan(plan)
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
        context = await _fork_and_start(
            workspace=workspace,
            source=source,
            plan=plan,
            source_point=source_point,
            new_run_id=new_run_id,
            graph=graph,
            lifecycle=lifecycle,
            attempt=attempt,
            child_execution=child_execution,
            heartbeat_task=heartbeat_task,
        )
        return context
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


async def finalize_handed_off_recovery_attempt(
    *,
    workspace: str,
    new_run_id: str,
    graph: Any,
) -> Any:
    """为已完成 lifecycle handoff 但尚未 fork 的 child 补齐 durable fork 状态。"""

    attempt = await get_recovery_attempt(workspace, new_run_id)
    if attempt is None or attempt.status is not RecoveryAttemptStatus.HANDED_OFF:
        return attempt
    source = await get_execution(workspace, attempt.source_run_id)
    if source is None:
        raise RecoveryExecutionError(
            "SOURCE_EXECUTION_NOT_FOUND",
            "RecoveryAttempt 的 source execution 不存在。",
        )
    source_point = await get_recovery_point(workspace, attempt.source_recovery_point_id)
    if source_point is None:
        raise RecoveryExecutionError(
            "INVALID_RECOVERY_POINT",
            "RecoveryAttempt 的 source RecoveryPoint 不存在。",
        )
    plan = RecoveryPlan(
        source_run_id=source.run_id,
        thread_id=source.thread_id,
        decision="ready_native",
        strategy=attempt.strategy,
        recovery_point_id=attempt.source_recovery_point_id,
        checkpoint_id=attempt.source_checkpoint_id,
        checkpoint_ns=attempt.source_checkpoint_ns,
        next_nodes=list(source_point.next_nodes),
        reason_code="RECOVERY_RECONCILED",
        reason="reconciled handed-off recovery",
        lifecycle_revision=source_point.lifecycle_revision,
        workspace_revision=source_point.workspace_revision,
        workspace_snapshot_hash=source_point.workspace_snapshot_hash,
    )
    _validate_root_plan(plan)
    lifecycle = load_application_lifecycle(workspace)
    child_execution = await get_execution(workspace, new_run_id)
    if child_execution is None:
        raise RecoveryExecutionError(
            "RECOVERY_EXECUTION_NOT_FOUND",
            "HANDED_OFF recovery 的 child execution 不存在。",
        )
    identity = current_backend_instance()
    await takeover_pre_runtime_recovery_lease(
        workspace=workspace,
        new_run_id=new_run_id,
        expected_attempt_status={RecoveryAttemptStatus.HANDED_OFF},
        new_owner_backend_instance_id=identity.instance_id,
        new_owner_pid=identity.pid,
        lease_ttl_seconds=Settings.from_env().execution_recovery_lease_ttl_seconds,
    )
    heartbeat_task = _start_recovery_heartbeat(
        workspace=workspace,
        run_id=new_run_id,
        owner_backend_instance_id=identity.instance_id,
    )
    try:
        context = await _fork_and_start(
            workspace=workspace,
            source=source,
            plan=plan,
            source_point=source_point,
            new_run_id=new_run_id,
            graph=graph,
            lifecycle=lifecycle,
            attempt=attempt,
            child_execution=child_execution,
            heartbeat_task=heartbeat_task,
        )
        return context
    except Exception as exc:
        await stop_execution_heartbeat(heartbeat_task)
        await _handle_pre_runtime_failure(
            workspace=workspace,
            new_run_id=new_run_id,
            attempt=await get_recovery_attempt(workspace, new_run_id),
            error_code=_recovery_error_code(exc),
            handoff_completed=True,
        )
        raise


async def _fork_and_start(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    plan: RecoveryPlan,
    source_point: RecoveryPoint,
    new_run_id: str,
    graph: Any,
    lifecycle: Any,
    attempt: RecoveryAttempt,
    child_execution: DurableExecutionRecord,
    heartbeat_task: asyncio.Task[None] | None,
) -> NativeRecoveryRuntimeContext:
    """只写 runtime identity 的 fork checkpoint，并在 durable point 后标记 STARTED。"""

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
        "resume_from": "",
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
    if fork_next != plan.next_nodes:
        raise RecoveryExecutionError(
            "RECOVERY_FORK_NEXT_MISMATCH",
            "fork checkpoint 的 nextNodes 与 RecoveryPlan 不一致。",
        )
    values = getattr(fork_snapshot, "values", {})
    values = values if isinstance(values, dict) else {}
    if str(values.get("active_run_id") or "") != new_run_id:
        raise RecoveryExecutionError(
            "RECOVERY_FORK_IDENTITY_MISMATCH",
            "fork checkpoint 未写入 child active_run_id。",
        )
    lifecycle_revision = getattr(lifecycle, "revision", None)
    point = RecoveryPoint(
        recovery_point_id=f"recovery-point-{uuid4().hex}",
        run_id=new_run_id,
        thread_id=source.thread_id,
        kind=RecoveryPointKind.CHECKPOINT,
        checkpoint_id=fork_checkpoint_id,
        checkpoint_ns="",
        graph_node=plan.next_nodes[0],
        completed_node=None,
        next_nodes=list(plan.next_nodes),
        phase=str(values.get("phase") or plan.next_nodes[0]),
        state_status=str(values.get("status") or "running"),
        lifecycle_revision=lifecycle_revision
        if lifecycle_revision is not None
        else plan.lifecycle_revision,
        workspace_revision=plan.workspace_revision,
        workspace_snapshot_hash=plan.workspace_snapshot_hash,
        captured_at=datetime.now(timezone.utc),
    )
    await insert_recovery_point(workspace=workspace, point=point)
    await update_recovery_attempt(
        workspace=workspace,
        new_run_id=new_run_id,
        status=RecoveryAttemptStatus.STARTED,
        replay_checkpoint_id=fork_checkpoint_id,
        replay_checkpoint_ns="",
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
        source_recovery_point=source_point,
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


def _handoff_lifecycle(
    workspace: str,
    *,
    source: DurableExecutionRecord,
    plan: RecoveryPlan,
    new_run_id: str,
) -> Any:
    """按 execution kind 选择 Workbench 或 Application Planning 的 handoff。"""

    if source.execution_kind == "application_planning":
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
        phase=plan.next_nodes[0],
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


def _require_native_plan(plan: RecoveryPlan) -> None:
    """把 P0.3A 的非 Native 结果转换为无副作用的结构化拒绝。"""

    if plan.decision.value == "ready_native" and plan.strategy is RecoveryStrategy.NATIVE_CHECKPOINT:
        return
    if plan.decision.value == "requires_handler":
        code = "RECOVERY_REQUIRES_HANDLER"
    elif plan.decision.value == "state_drift":
        code = plan.reason_code or "RECOVERY_STATE_DRIFT"
    elif plan.decision.value == "awaiting_user":
        code = plan.reason_code or "SOURCE_AWAITING_USER"
    else:
        code = plan.reason_code or "RECOVERY_NOT_RECOVERABLE"
    raise RecoveryExecutionError(code, plan.reason)


def _validate_root_plan(plan: RecoveryPlan) -> None:
    """限制 P0.3B 只处理 root namespace 与单一 successor。"""

    if plan.checkpoint_ns != "":
        raise RecoveryExecutionError(
            "NATIVE_SUBGRAPH_REPLAY_UNSUPPORTED",
            "P0.3B 暂不支持非 root checkpoint namespace replay。",
        )
    if len(plan.next_nodes) != 1:
        raise RecoveryExecutionError(
            "NATIVE_PARALLEL_REPLAY_UNSUPPORTED",
            "P0.3B 暂不支持并行 next nodes replay。",
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
    """在 Graph 尚未交给 Runtime 前按 PREPARING/HANDED_OFF 语义收口 child。"""

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
    lease = await get_execution_lease(workspace, new_run_id)
    if lease is not None:
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
