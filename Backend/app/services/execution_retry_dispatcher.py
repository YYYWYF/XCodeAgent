"""P1.0 Generic Retry 的失败现场校验与既有 handler 选择。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryExecutionError,
    RecoveryPoint,
    RecoveryPointKind,
)
from app.persistence.execution_recovery import get_execution, get_recovery_point
from app.protocols.workflow.request import workflow_run_inputs


RetryHandler = Literal["retry_failed_tasks", "retry_code_review"]


@dataclass(frozen=True, slots=True)
class RetryDispatchPlan:
    """保存 Backend 选择出的旧 handler 及其内部 Workflow 请求。"""

    source_run_id: str
    thread_id: str
    owner_session_id: str | None
    handler: RetryHandler
    internal_payload: dict[str, Any]


class RetryAdapter(Protocol):
    """定义一个只负责构造并试运行旧 handler 请求的 Adapter。"""

    name: RetryHandler

    async def prepare(
        self,
        source: DurableExecutionRecord,
        snapshot: Any,
    ) -> dict[str, Any] | None:
        """根据失败现场返回可被现有 Workflow parser 接受的内部请求。"""


class CodeReviewRetryAdapter:
    """把代码审查失败现场映射到既有 retry_code_review action。"""

    name: RetryHandler = "retry_code_review"

    async def prepare(
        self,
        source: DurableExecutionRecord,
        snapshot: Any,
    ) -> dict[str, Any] | None:
        """通过现有 Workflow parser 判断审查重试是否适用于当前现场。"""

        payload = _retry_payload(source, snapshot, self.name)
        try:
            parsed = workflow_run_inputs(payload)
        except ValueError:
            return None
        return payload if parsed.get("workflow_action") == self.name else None


class FailedTasksRetryAdapter:
    """把可由 Build handler 处理的失败现场映射到既有 retry_failed_tasks action。"""

    name: RetryHandler = "retry_failed_tasks"

    async def prepare(
        self,
        source: DurableExecutionRecord,
        snapshot: Any,
    ) -> dict[str, Any] | None:
        """先确认 Build 恢复能力，再通过现有 parser 判断失败任务重试是否适用。"""

        if not _has_failed_tasks_retry_evidence(snapshot):
            return None

        payload = _retry_payload(source, snapshot, self.name)
        try:
            parsed = workflow_run_inputs(payload)
        except ValueError:
            return None
        if parsed.get("workflow_action") != self.name:
            return None
        return payload


RETRY_ADAPTERS: tuple[RetryAdapter, ...] = (
    CodeReviewRetryAdapter(),
    FailedTasksRetryAdapter(),
)


async def prepare_retry_current_failure(
    *,
    workspace: str,
    source_run_id: str,
    graph: Any,
) -> RetryDispatchPlan:
    """读取精确失败 checkpoint，并由 Backend 选择一个既有重试 handler。"""

    source = await get_execution(workspace, source_run_id)
    if source is None:
        raise RecoveryExecutionError(
            "SOURCE_EXECUTION_NOT_FOUND",
            "source execution 不存在。",
        )
    if _workspace_identity(source.workspace) != _workspace_identity(workspace):
        raise RecoveryExecutionError(
            "INVALID_EXECUTION_RECOVERY_REQUEST",
            "workspaceRoot 与 source execution 的 workspace 不一致。",
        )
    if source.status is not DurableExecutionStatus.FAILED:
        raise RecoveryExecutionError(
            "RETRY_SOURCE_NOT_FAILED",
            "通用重试只接受 failed execution。",
        )
    if source.execution_kind != "workbench":
        raise RecoveryExecutionError(
            "RETRY_HANDLER_NOT_AVAILABLE",
            "当前失败暂未接入通用重试，请继续使用原有重试入口。",
        )
    if not source.last_recovery_point_id:
        raise RecoveryExecutionError(
            "RETRY_SOURCE_CHECKPOINT_INVALID",
            "当前失败没有可用的精确 RecoveryPoint。",
        )

    point = await get_recovery_point(workspace, source.last_recovery_point_id)
    if (
        point is None
        or point.kind is not RecoveryPointKind.CHECKPOINT
        or point.run_id != source.run_id
        or point.thread_id != source.thread_id
        or not point.checkpoint_id
    ):
        raise RecoveryExecutionError(
            "RETRY_SOURCE_CHECKPOINT_INVALID",
            "当前失败的 RecoveryPoint 无效，无法安全重试。",
        )
    snapshot = await _read_source_snapshot(graph, source, point)
    _validate_source_snapshot(source, point, snapshot)
    await _validate_current_thread_head(
        graph=graph,
        source=source,
        point=point,
    )

    for adapter in RETRY_ADAPTERS:
        payload = await adapter.prepare(source, snapshot)
        if payload is not None:
            return RetryDispatchPlan(
                source_run_id=source.run_id,
                thread_id=source.thread_id,
                owner_session_id=source.owner_session_id,
                handler=adapter.name,
                internal_payload=payload,
            )
    raise RecoveryExecutionError(
        "RETRY_HANDLER_NOT_AVAILABLE",
        "当前失败暂未接入通用重试，请继续使用原有重试入口。",
    )


async def _read_source_snapshot(
    graph: Any,
    source: DurableExecutionRecord,
    point: RecoveryPoint,
) -> Any:
    """只从 source 记录的 checkpoint identity 读取失败现场。"""

    if not hasattr(graph, "aget_state"):
        raise RecoveryExecutionError(
            "RETRY_SOURCE_CHECKPOINT_INVALID",
            "当前 Graph 不支持读取失败 checkpoint。",
        )
    config = {
        "configurable": {
            "thread_id": source.thread_id,
            "checkpoint_ns": point.checkpoint_ns,
            "checkpoint_id": point.checkpoint_id,
        }
    }
    try:
        return await graph.aget_state(config)
    except Exception as exc:  # noqa: BLE001 - 协议层统一转换 checkpoint 读取失败
        raise RecoveryExecutionError(
            "RETRY_SOURCE_CHECKPOINT_INVALID",
            "当前失败的 checkpoint 无法读取，无法安全重试。",
        ) from exc


async def _validate_current_thread_head(
    *,
    graph: Any,
    source: DurableExecutionRecord,
    point: RecoveryPoint,
) -> None:
    """读取当前 thread head，确认 source execution 仍是最新 owner。"""

    if not hasattr(graph, "aget_state"):
        raise RecoveryExecutionError(
            "RETRY_SOURCE_CHECKPOINT_INVALID",
            "当前 Graph 无法验证失败 execution 的最新状态。",
        )
    config = {
        "configurable": {
            "thread_id": source.thread_id,
            "checkpoint_ns": point.checkpoint_ns,
        }
    }
    try:
        latest = await graph.aget_state(config)
    except Exception as exc:  # noqa: BLE001 - 协议层统一转换 thread head 读取失败
        raise RecoveryExecutionError(
            "RETRY_SOURCE_CHECKPOINT_INVALID",
            "无法读取当前 Graph thread 状态。",
        ) from exc

    values = getattr(latest, "values", {})
    if not isinstance(values, dict):
        raise RecoveryExecutionError(
            "RETRY_SOURCE_STALE",
            "无法证明当前失败仍是最新执行。",
        )
    if str(values.get("active_run_id") or "") != source.run_id:
        raise RecoveryExecutionError(
            "RETRY_SOURCE_STALE",
            "当前失败已经被新的执行替代，请刷新后查看最新状态。",
        )


def _validate_source_snapshot(
    source: DurableExecutionRecord,
    point: RecoveryPoint,
    snapshot: Any,
) -> None:
    """校验 source、RecoveryPoint 与 LangGraph snapshot 的三方 identity。"""

    raw_config = getattr(snapshot, "config", {})
    config = raw_config if isinstance(raw_config, dict) else {}
    configurable = config.get("configurable", {})
    configurable = configurable if isinstance(configurable, dict) else {}
    snapshot_thread_id = str(configurable.get("thread_id") or "")
    snapshot_checkpoint_ns = str(configurable.get("checkpoint_ns") or "")
    snapshot_checkpoint_id = str(configurable.get("checkpoint_id") or "")
    if (
        snapshot_thread_id != source.thread_id
        or snapshot_checkpoint_ns != point.checkpoint_ns
        or snapshot_checkpoint_id != point.checkpoint_id
    ):
        raise RecoveryExecutionError(
            "RETRY_SOURCE_CHECKPOINT_INVALID",
            "source checkpoint identity 已偏离 RecoveryPoint。",
        )
    values = getattr(snapshot, "values", {})
    values = values if isinstance(values, dict) else {}
    if str(values.get("active_run_id") or "") != source.run_id:
        raise RecoveryExecutionError(
            "RETRY_SOURCE_STALE",
            "当前失败已经被新的执行替代，请刷新后查看最新状态。",
        )


def _has_failed_tasks_retry_evidence(snapshot: Any) -> bool:
    """只根据明确的 Build 恢复证据判断失败任务 Adapter 是否具备能力。"""

    values = getattr(snapshot, "values", {})
    if not isinstance(values, dict):
        return False
    build_summary = values.get("build_summary")
    if not isinstance(build_summary, dict):
        return False
    if build_summary.get("recovery_available") is True:
        return True
    if build_summary.get("retry_available") is True:
        return True
    retryable_failures = build_summary.get("retryable_failures")
    return (
        isinstance(retryable_failures, int)
        and not isinstance(retryable_failures, bool)
        and retryable_failures > 0
    )


def _retry_payload(
    source: DurableExecutionRecord,
    snapshot: Any,
    handler: RetryHandler,
) -> dict[str, Any]:
    """构造不向 Frontend 暴露的 Backend-owned 旧 handler payload。"""

    values = getattr(snapshot, "values", {})
    values = dict(values) if isinstance(values, dict) else {}
    resume_state = {
        "runId": source.run_id,
        "threadId": source.thread_id,
        "summary": {
            "status": "failed",
            "phase": values.get("phase"),
        },
        "state": dict(values),
        "result": dict(values),
        "events": [],
    }
    forwarded_props: dict[str, Any] = {
        "workspaceRoot": source.workspace,
        "sessionId": source.owner_session_id,
        "application": {
            "id": source.project_id or "",
            "workspaceRoot": source.workspace,
        },
        "workflowAction": handler,
        "resumeExecutionRunId": source.run_id,
        "resumeState": resume_state,
    }
    if source.workflow_scope:
        forwarded_props["workflowScope"] = source.workflow_scope
    return {
        "threadId": source.thread_id,
        "runId": f"workflow-{uuid4().hex[:12]}",
        "messages": [
            {
                "role": "user",
                "content": "重试当前失败任务。",
            }
        ],
        "forwardedProps": forwarded_props,
    }


def _workspace_identity(value: str) -> str:
    """把请求和持久记录中的 workspace 规范化为同一比较身份。"""

    return str(Path(value).expanduser().resolve(strict=False))


__all__ = [
    "RETRY_ADAPTERS",
    "RetryAdapter",
    "RetryDispatchPlan",
    "prepare_retry_current_failure",
]
