"""按 LangGraph committed checkpoint 收敛 Durable 与 ApplicationLifecycle mirror。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domain.application_lifecycle import (
    ApplicationLifecycle,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    WorkbenchExecutionStatus,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
)
from app.persistence.execution_recovery import (
    reconcile_interrupted_execution_status,
)
from app.protocols.workflow.lifecycle import (
    project_workflow_lifecycle_boundary,
    stop_workflow_lifecycle,
)
from app.services.application_lifecycle import (
    complete_workbench_execution,
    load_application_lifecycle,
    persist_application_lifecycle_transition,
)


async def reconcile_interrupted_execution_state(
    *,
    workspace: str | Path,
    source: DurableExecutionRecord,
    status: DurableExecutionStatus,
    snapshot: Any | None,
) -> DurableExecutionRecord | None:
    """先对账 lifecycle mirror，再按同一 Graph 终态更新 Durable execution。"""

    if source.status is not DurableExecutionStatus.INTERRUPTED:
        raise ValueError("只有 INTERRUPTED execution 可以进行 checkpoint 终态对账。")
    values = _snapshot_values(snapshot)
    if source.execution_kind == "workbench":
        _reconcile_workbench_lifecycle(
            workspace,
            source=source,
            status=status,
            values=values,
            snapshot=snapshot,
        )
    else:
        _reconcile_application_planning_lifecycle(
            workspace,
            source=source,
            status=status,
            values=values,
        )
    # lifecycle 写盘成功后才收口 Durable；若此处再次崩溃，下一次启动仍会看到
    # INTERRUPTED source，并可幂等重做 mirror 对账，而不会重跑任何 Graph Node。
    return await reconcile_interrupted_execution_status(
        workspace=workspace,
        run_id=source.run_id,
        status=status,
    )


def _reconcile_workbench_lifecycle(
    workspace: str | Path,
    *,
    source: DurableExecutionRecord,
    status: DurableExecutionStatus,
    values: dict[str, Any],
    snapshot: Any | None,
) -> ApplicationLifecycle | None:
    """通过既有 Workbench lifecycle API 收敛 execution、交互和资源锁。"""

    lifecycle = load_application_lifecycle(workspace)
    if lifecycle is None:
        return None
    active = lifecycle.active_executions.get(source.run_id)
    if status is DurableExecutionStatus.COMPLETED:
        if active is None and lifecycle.active_run_id != source.run_id and not _owns_lock(
            lifecycle,
            source.run_id,
        ):
            return lifecycle
        return complete_workbench_execution(
            workspace,
            run_id=source.run_id,
            phase=_phase(values, source),
            missing_ok=True,
        )
    if active is None:
        # 非完成终态不能凭 checkpoint 重新伪造已被删除的 Workbench execution；
        # Durable 仍会在下方收口，避免触碰其他运行的 active_run_id 或锁。
        return lifecycle
    if status is DurableExecutionStatus.AWAITING_USER:
        if (
            active.status is WorkbenchExecutionStatus.AWAITING_USER
            and active.pending_interaction is not None
        ):
            return lifecycle
        update = _lifecycle_update(
            values,
            status="requires_user_input",
            phase=_phase(values, source),
            snapshot=snapshot,
        )
        projected = project_workflow_lifecycle_boundary(
            workspace,
            run_id=source.run_id,
            node_name=_phase(values, source),
            update=update,
        )
        if projected is not None:
            return load_application_lifecycle(workspace) or lifecycle
        return lifecycle
    if status is DurableExecutionStatus.FAILED:
        if active.status is WorkbenchExecutionStatus.FAILED and active.error is not None:
            return lifecycle
        update = _lifecycle_update(
            values,
            status="failed",
            phase=_phase(values, source),
            snapshot=snapshot,
        )
        projected = project_workflow_lifecycle_boundary(
            workspace,
            run_id=source.run_id,
            node_name=_phase(values, source),
            update=update,
        )
        if projected is not None:
            return load_application_lifecycle(workspace) or lifecycle
        return lifecycle
    if status in {
        DurableExecutionStatus.CANCELLED,
        DurableExecutionStatus.STOPPED,
    }:
        if active.status is WorkbenchExecutionStatus.STOPPED:
            return lifecycle
        stop_workflow_lifecycle(
            workspace,
            run_id=source.run_id,
            phase=_phase(values, source),
        )
        return load_application_lifecycle(workspace) or lifecycle
    raise ValueError(f"不支持的 Workbench checkpoint 终态：{status.value}")


def _reconcile_application_planning_lifecycle(
    workspace: str | Path,
    *,
    source: DurableExecutionRecord,
    status: DurableExecutionStatus,
    values: dict[str, Any],
) -> ApplicationLifecycle | None:
    """验证 Planning 节点已写下的生命周期，缺口时仅用现有状态 API 补写终态。"""

    lifecycle = load_application_lifecycle(workspace)
    if lifecycle is None:
        return lifecycle
    if lifecycle.active_run_id not in {None, source.run_id}:
        raise ValueError("ApplicationLifecycle active_run_id 与 checkpoint source 不一致。")
    committed = _committed_planning_lifecycle(values)
    if committed is not None:
        committed_stage, committed_status, committed_run_id = committed
        if committed_run_id not in {None, source.run_id}:
            raise ValueError("checkpoint lifecycle 镜像的 active_run_id 与 source 不一致。")
        if (
            lifecycle.initialization.stage is committed_stage
            and lifecycle.initialization.status is committed_status
            and (
                committed_run_id is None
                or lifecycle.active_run_id == committed_run_id
            )
        ):
            # 业务节点已经把同一份 lifecycle 写盘；Graph terminal 只能验证它，
            # 不能把模板生成等仍在异步收口的阶段误标成 completed。
            return lifecycle
        return persist_application_lifecycle_transition(
            workspace,
            stage=committed_stage,
            status=committed_status,
            active_run_id=committed_run_id or source.run_id,
            error=lifecycle.error if committed_status is ApplicationLifecycleStatus.FAILED else None,
        )
    if status is DurableExecutionStatus.COMPLETED:
        # 当前 planning checkpoint 没有镜像载荷时，保留业务节点已经写下的 lifecycle；
        # Durable terminal status 不等价于 ApplicationLifecycle 已完成。
        return lifecycle
    desired = {
        DurableExecutionStatus.AWAITING_USER: ApplicationLifecycleStatus.AWAITING_USER,
        DurableExecutionStatus.FAILED: ApplicationLifecycleStatus.FAILED,
        DurableExecutionStatus.CANCELLED: ApplicationLifecycleStatus.CANCELLED,
        DurableExecutionStatus.STOPPED: ApplicationLifecycleStatus.STOPPED,
    }.get(status)
    if desired is None:
        raise ValueError(f"不支持的 Planning checkpoint 终态：{status.value}")
    if (
        lifecycle.initialization.status is desired
        and lifecycle.active_run_id == source.run_id
    ):
        return lifecycle
    return persist_application_lifecycle_transition(
        workspace,
        stage=lifecycle.initialization.stage,
        status=desired,
        active_run_id=source.run_id,
        error=lifecycle.error if desired is ApplicationLifecycleStatus.FAILED else None,
    )


def _committed_planning_lifecycle(
    values: dict[str, Any],
) -> tuple[ApplicationLifecycleStage, ApplicationLifecycleStatus, str | None] | None:
    """解析 checkpoint 携带的 Planning lifecycle 镜像，供 API 对账而非直接改文件。"""

    payload = values.get("lifecycle")
    if not isinstance(payload, dict):
        return None
    initialization = payload.get("initialization")
    if not isinstance(initialization, dict):
        return None
    stage_value = str(initialization.get("stage") or "").strip()
    status_value = str(initialization.get("status") or "").strip()
    if not stage_value or not status_value:
        return None
    try:
        stage = ApplicationLifecycleStage(stage_value)
        lifecycle_status = ApplicationLifecycleStatus(status_value)
    except ValueError as exc:
        raise ValueError("checkpoint 携带了无法识别的 ApplicationLifecycle 镜像。") from exc
    active_run_id = str(
        payload.get("activeRunId") or payload.get("active_run_id") or ""
    ).strip() or None
    return stage, lifecycle_status, active_run_id


def _snapshot_values(snapshot: Any | None) -> dict[str, Any]:
    """从 resolver 已验证的 checkpoint 提取只读 Graph State。"""

    values = getattr(snapshot, "values", {}) if snapshot is not None else {}
    return values if isinstance(values, dict) else {}


def _phase(values: dict[str, Any], source: DurableExecutionRecord) -> str:
    """确定 lifecycle boundary 所需的当前业务阶段，不决定恢复 target。"""

    return str(values.get("phase") or source.current_node or source.first_node or "workflow")


def _lifecycle_update(
    values: dict[str, Any],
    *,
    status: str,
    phase: str,
    snapshot: Any | None,
) -> dict[str, Any]:
    """复制 checkpoint State 为既有 lifecycle boundary 准备终态投影。"""

    update = dict(values)
    update["phase"] = phase
    update["status"] = status
    if status == "requires_user_input" and not isinstance(update.get("clarification"), dict):
        interrupt = _snapshot_interrupt_payload(snapshot)
        if interrupt:
            update["clarification"] = interrupt
    return update


def _snapshot_interrupt_payload(snapshot: Any | None) -> dict[str, Any]:
    """提取原生 interrupt 的结构化 payload，保持可恢复交互而不重跑节点。"""

    for task in getattr(snapshot, "tasks", ()) or ():
        for interrupt in getattr(task, "interrupts", ()) or ():
            payload = getattr(interrupt, "value", interrupt)
            if isinstance(payload, dict):
                result = dict(payload)
                result.setdefault("mode", "plan_adjustment")
                result.setdefault("status", "requires_user_input")
                return result
            if payload is not None:
                return {
                    "mode": "plan_adjustment",
                    "status": "requires_user_input",
                    "message": str(payload),
                }
    return {}


def _owns_lock(lifecycle: ApplicationLifecycle, run_id: str) -> bool:
    """判断 lifecycle 是否仍保留属于 source 的任一资源锁。"""

    locks = lifecycle.resource_locks
    candidates = [locks.application, *locks.pages.values(), *locks.endpoints.values()]
    candidates.extend(locks.api_contracts.values())
    candidates.extend(locks.data_sources.values())
    return any(lock is not None and lock.run_id == run_id for lock in candidates)
