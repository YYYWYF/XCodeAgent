"""Durable Execution Recovery 的旁路观测服务。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, TypeVar
from uuid import uuid4

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryPoint,
    RecoveryPointKind,
)
from app.persistence.execution_recovery import (
    initialize_execution_recovery_store,
    insert_execution,
    insert_recovery_point,
    update_execution_node,
    update_execution_status,
)
from app.services.application_lifecycle import load_application_lifecycle


logger = logging.getLogger("uvicorn.error")
_ObservationResult = TypeVar("_ObservationResult")


def _utc_now() -> datetime:
    """返回恢复记录使用的当前 UTC 时间。"""

    return datetime.now(timezone.utc)


async def best_effort_recovery_observation(
    *,
    operation: str,
    workspace: str | None,
    run_id: str,
    thread_id: str,
    workflow_scope: str | None,
    callback: Callable[[], Awaitable[_ObservationResult]],
) -> _ObservationResult | None:
    """执行旁路观测并将持久化故障降级为带上下文的 warning。"""

    if not workspace:
        return None
    try:
        return await callback()
    except Exception as exc:
        logger.warning(
            "recovery.observation.failed operation=%s runId=%s threadId=%s "
            "workflowScope=%s error=%s",
            operation,
            run_id,
            thread_id,
            workflow_scope,
            exc,
            exc_info=True,
        )
        return None


async def observe_execution_started(
    *,
    workspace: str | None,
    project_id: str | None,
    thread_id: str,
    run_id: str,
    workflow_scope: str | None,
    first_node: str,
) -> DurableExecutionRecord | None:
    """登记真实 Graph 执行，并将 planning/workbench 统一映射为记录类型。"""

    if not workspace:
        return None
    now = _utc_now()
    record = DurableExecutionRecord(
        run_id=run_id,
        thread_id=thread_id,
        workspace=workspace,
        project_id=project_id,
        execution_kind=(
            "application_planning"
            if workflow_scope == "application_planning"
            else "workbench"
        ),
        workflow_scope=workflow_scope,
        first_node=first_node,
        current_node=first_node,
        status=DurableExecutionStatus.RUNNING,
        started_at=now,
        updated_at=now,
    )
    await initialize_execution_recovery_store(workspace)
    persisted = await insert_execution(record)
    logger.info(
        "recovery.execution.started runId=%s threadId=%s workflowScope=%s "
        "firstNode=%s",
        run_id,
        thread_id,
        workflow_scope,
        first_node,
    )
    return persisted


async def observe_node_started(
    *,
    workspace: str | None,
    run_id: str,
    thread_id: str,
    workflow_scope: str | None,
    node_name: str,
) -> None:
    """记录当前开始执行的顶层 Graph Node。"""

    if not workspace:
        return
    await update_execution_node(
        workspace=workspace,
        run_id=run_id,
        node_name=node_name,
    )
    logger.info(
        "recovery.node.started runId=%s threadId=%s workflowScope=%s node=%s",
        run_id,
        thread_id,
        workflow_scope,
        node_name,
    )


async def capture_recovery_point(
    *,
    graph: Any,
    config: dict[str, Any],
    workspace: str | None,
    thread_id: str,
    run_id: str,
    workflow_scope: str | None,
    completed_node: str | None = None,
    first_node: str | None = None,
    snapshot: Any | None = None,
) -> RecoveryPoint | None:
    """从真实 StateSnapshot 捕获轻量 checkpoint 索引，不复制完整 Graph State。"""

    if not workspace:
        return None
    if snapshot is None:
        if not hasattr(graph, "aget_state"):
            return None
        snapshot = await graph.aget_state(config)

    raw_config = getattr(snapshot, "config", {})
    snapshot_config = raw_config if isinstance(raw_config, dict) else {}
    configurable = snapshot_config.get("configurable", {})
    configurable = configurable if isinstance(configurable, dict) else {}
    raw_checkpoint_id = configurable.get("checkpoint_id")
    checkpoint_id = str(raw_checkpoint_id) if raw_checkpoint_id else None
    checkpoint_ns = str(configurable.get("checkpoint_ns") or "")
    raw_next_nodes = getattr(snapshot, "next", ()) or ()
    next_nodes = [str(node_name) for node_name in raw_next_nodes]
    values = getattr(snapshot, "values", {}) or {}
    values = dict(values) if isinstance(values, dict) else {}
    phase = values.get("phase")
    state_status = values.get("status")
    lifecycle_revision = _lifecycle_revision(workspace)
    graph_node = (
        str(completed_node)
        if completed_node
        else (next_nodes[0] if next_nodes else None)
    )
    point = RecoveryPoint(
        recovery_point_id=f"recovery-point-{uuid4().hex}",
        run_id=run_id,
        thread_id=thread_id,
        kind=(
            RecoveryPointKind.CHECKPOINT
            if checkpoint_id
            else RecoveryPointKind.ENTRY
        ),
        checkpoint_id=checkpoint_id,
        checkpoint_ns=checkpoint_ns,
        graph_node=graph_node or first_node,
        completed_node=completed_node,
        next_nodes=next_nodes or ([first_node] if first_node and not completed_node else []),
        phase=str(phase) if phase is not None else None,
        state_status=str(state_status) if state_status is not None else None,
        lifecycle_revision=lifecycle_revision,
        workspace_revision=_optional_string(values.get("workspace_revision")),
        workspace_snapshot_hash=_optional_string(values.get("workspace_snapshot_hash")),
        captured_at=_utc_now(),
    )
    persisted = await insert_recovery_point(workspace=workspace, point=point)
    logger.info(
        "recovery.point.captured runId=%s threadId=%s workflowScope=%s "
        "node=%s checkpointId=%s nextNodes=%s",
        run_id,
        thread_id,
        workflow_scope,
        completed_node,
        checkpoint_id,
        next_nodes,
    )
    return persisted


async def observe_execution_finished(
    *,
    workspace: str | None,
    run_id: str,
    thread_id: str,
    workflow_scope: str | None,
    status: DurableExecutionStatus,
) -> None:
    """记录 Graph 正常结束或进入等待用户确认的明确终态。"""

    if not workspace:
        return
    await update_execution_status(
        workspace=workspace,
        run_id=run_id,
        status=status,
        ended=True,
    )
    logger.info(
        "recovery.execution.finished runId=%s threadId=%s workflowScope=%s status=%s",
        run_id,
        thread_id,
        workflow_scope,
        status.value,
    )


async def observe_execution_failed(
    *,
    workspace: str | None,
    run_id: str,
    thread_id: str,
    workflow_scope: str | None,
) -> None:
    """记录未处理异常对应的失败终态。"""

    await _observe_terminal_status(
        workspace=workspace,
        run_id=run_id,
        thread_id=thread_id,
        workflow_scope=workflow_scope,
        status=DurableExecutionStatus.FAILED,
        log_name="recovery.execution.failed",
    )


async def observe_execution_cancelled(
    *,
    workspace: str | None,
    run_id: str,
    thread_id: str,
    workflow_scope: str | None,
) -> None:
    """记录 asyncio 取消对应的取消终态，并保留原取消异常向上传播。"""

    await _observe_terminal_status(
        workspace=workspace,
        run_id=run_id,
        thread_id=thread_id,
        workflow_scope=workflow_scope,
        status=DurableExecutionStatus.CANCELLED,
        log_name="recovery.execution.cancelled",
    )


def durable_execution_status(
    *,
    result: dict[str, Any],
    summary: dict[str, Any],
) -> DurableExecutionStatus:
    """把公开运行结果映射为恢复层状态，不触发任何业务生命周期转换。"""

    observed_status = str(
        result.get("status") or summary.get("status") or ""
    ).lower()
    if observed_status in {"requires_user_input", "awaiting_user"}:
        return DurableExecutionStatus.AWAITING_USER
    if observed_status == "failed":
        return DurableExecutionStatus.FAILED
    if observed_status == "cancelled":
        return DurableExecutionStatus.CANCELLED
    if observed_status == "stopped":
        return DurableExecutionStatus.STOPPED
    return DurableExecutionStatus.COMPLETED


def _lifecycle_revision(workspace: str) -> int | None:
    """读取可选 lifecycle revision；生命周期读取失败不阻断恢复现场记录。"""

    try:
        lifecycle = load_application_lifecycle(workspace)
    except (OSError, ValueError):
        return None
    return lifecycle.revision if lifecycle is not None else None


def _optional_string(value: Any) -> str | None:
    """将状态中的可选字段压缩为非空字符串或 None。"""

    if value is None:
        return None
    normalized = str(value)
    return normalized if normalized else None


async def _observe_terminal_status(
    *,
    workspace: str | None,
    run_id: str,
    thread_id: str,
    workflow_scope: str | None,
    status: DurableExecutionStatus,
    log_name: str,
) -> None:
    """写入失败或取消状态并输出不含敏感 State 的结构化上下文。"""

    if not workspace:
        return
    await update_execution_status(
        workspace=workspace,
        run_id=run_id,
        status=status,
        ended=True,
    )
    logger.info(
        "%s runId=%s threadId=%s workflowScope=%s status=%s",
        log_name,
        run_id,
        thread_id,
        workflow_scope,
        status.value,
    )
