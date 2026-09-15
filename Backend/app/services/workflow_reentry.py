"""Workflow-wide Node Re-entry 的 authority 解析与统一计划合同。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    NodeEntryBoundary,
    RecoveryDecision,
    RecoveryExecutionError,
    RecoveryLifecycleOwnershipMode,
    RecoveryPlan,
    RecoveryPoint,
    RecoveryPointKind,
    RecoveryStrategy,
    WorkflowReentryContextAuthority,
    WorkflowReentryContextAuthorityKind,
    WorkflowReentryLifecycleAuthority,
    WorkflowReentryPlan,
    WorkflowReentryReason,
)
from app.persistence.execution_recovery import (
    get_node_entry_boundary,
    insert_node_entry_boundary,
    insert_recovery_point,
)
from app.services.application_lifecycle import load_application_lifecycle
from app.services.execution_recovery import durable_execution_status
from app.services.execution_recovery_lineage import resolve_recovery_lineage_head


SYNTHETIC_WORKFLOW_ENTRY_NODE = "workflow_entry"
_RUNTIME_BINDING_KEYS = frozenset(
    {
        "active_run_id",
        "active_thread_id",
        "observability",
        "resume_from",
        # lifecycle 在 Native child fork 时会刷新 active run ownership；权威业务
        # 状态仍由工作区 lifecycle 文件持有，因此 Graph 中的投影不能污染语义摘要。
        "lifecycle",
    }
)


def workflow_entry(state: dict[str, Any]) -> dict[str, Any]:
    """提交无业务副作用的首节点前 checkpoint，并保持语义 State 原样。"""

    del state
    return {}


class FailureTargetResolver:
    """仅用 FAILED execution 与真实 LangGraph history 解析失败节点重入计划。"""

    async def resolve(
        self,
        *,
        workspace: str,
        source: DurableExecutionRecord,
        graph: Any,
    ) -> WorkflowReentryPlan:
        """验证异常失败合同并解析唯一精确的 Node Entry Boundary。"""

        if source.status is not DurableExecutionStatus.FAILED or source.failure is None:
            raise RecoveryExecutionError(
                "FAILED_EXCEPTION_EVIDENCE_MISSING",
                "failure_retry 只接受异常逃出 Node 后产生的 FAILED execution。",
            )
        target_node = str(source.current_node or "").strip()
        if not target_node:
            raise RecoveryExecutionError(
                "NODE_ENTRY_AUTHORITY_MISSING",
                "FAILED execution 缺少当前业务 Node。",
            )
        boundary = await resolve_node_entry_boundary(
            workspace=workspace,
            source=source,
            target_node=target_node,
            graph=graph,
        )
        lifecycle = load_application_lifecycle(workspace)
        return WorkflowReentryPlan(
            reason=WorkflowReentryReason.FAILURE_RETRY,
            execution_kind=source.execution_kind,
            target_node=target_node,
            thread_id=source.thread_id,
            source_run_id=source.run_id,
            context_authority=WorkflowReentryContextAuthority(
                kind=WorkflowReentryContextAuthorityKind.CHECKPOINT,
                boundary_id=boundary.boundary_id,
                source_run_id=boundary.source_run_id,
                thread_id=boundary.thread_id,
                target_node=boundary.target_node,
                checkpoint_id=boundary.checkpoint_id,
                checkpoint_ns=boundary.checkpoint_ns,
            ),
            lifecycle_authority=WorkflowReentryLifecycleAuthority(
                owner_run_id=source.run_id,
                revision=lifecycle.revision if lifecycle is not None else None,
            ),
            lineage_parent_run_id=source.run_id,
        )


@dataclass(frozen=True, slots=True)
class InterruptedTargetResolution:
    """保存最新 INTERRUPTED checkpoint 的唯一解释结果。"""

    kind: Literal["continue", "terminal", "awaiting_user", "needs_attention"]
    snapshot: Any | None
    reentry_plan: WorkflowReentryPlan | None
    reason_code: str
    reason: str
    terminal_status: DurableExecutionStatus | None = None


class InterruptedTargetResolver:
    """只用最新 source-owned root checkpoint 解析 INTERRUPTED 的位置。"""

    async def resolve(
        self,
        *,
        workspace: str,
        source: DurableExecutionRecord,
        graph: Any,
    ) -> InterruptedTargetResolution:
        """解释最新中断现场，不回退历史 checkpoint 或使用业务策略。"""

        if source.status is not DurableExecutionStatus.INTERRUPTED:
            return _interrupted_needs_attention(
                "INTERRUPTED_SOURCE_REQUIRED",
                "当前 source 不是 INTERRUPTED execution。",
            )
        try:
            lineage = await resolve_recovery_lineage_head(
                workspace,
                thread_id=source.thread_id,
                execution_kind=source.execution_kind,
            )
        except Exception as exc:
            return _interrupted_needs_attention(
                "RECOVERY_LINEAGE_UNAVAILABLE",
                "当前 INTERRUPTED source 的 lineage 无法安全解析。",
                cause=exc,
            )
        if (
            lineage.head is None
            or lineage.head.run_id != source.run_id
            or lineage.head.status is not DurableExecutionStatus.INTERRUPTED
            or lineage.state.value == "AMBIGUOUS"
        ):
            return _interrupted_needs_attention(
                "RECOVERY_SOURCE_NOT_CURRENT",
                "当前 INTERRUPTED source 不是唯一的 lineage head。",
            )

        history_reader = getattr(graph, "aget_state_history", None)
        if not callable(history_reader):
            return _interrupted_needs_attention(
                "INTERRUPTED_CHECKPOINT_AUTHORITY_MISSING",
                "当前 production Graph 无法读取 committed checkpoint history。",
            )
        try:
            async for snapshot in history_reader(
                {"configurable": {"thread_id": source.thread_id, "checkpoint_ns": ""}}
            ):
                config = _snapshot_config(snapshot)
                values = getattr(snapshot, "values", {})
                values = values if isinstance(values, dict) else {}
                if str(values.get("active_run_id") or "") != source.run_id:
                    continue
                if config is None or str(config.get("checkpoint_ns") or "") != "":
                    continue
                identity = _snapshot_identity(snapshot)
                if identity is None:
                    return _interrupted_needs_attention(
                        "INTERRUPTED_CHECKPOINT_IDENTITY_INVALID",
                        "最新 source-owned root checkpoint 缺少完整 identity.",
                        snapshot=snapshot,
                    )
                thread_id, checkpoint_ns, checkpoint_id = identity
                if thread_id != source.thread_id or checkpoint_ns != "":
                    return _interrupted_needs_attention(
                        "INTERRUPTED_CHECKPOINT_IDENTITY_INVALID",
                        "最新 source-owned checkpoint 不是当前 thread 的 root checkpoint。",
                        snapshot=snapshot,
                    )
                next_nodes = [
                    str(node) for node in (getattr(snapshot, "next", ()) or ())
                ]
                if _snapshot_has_native_interrupt(snapshot):
                    return InterruptedTargetResolution(
                        kind="awaiting_user",
                        snapshot=snapshot,
                        reentry_plan=None,
                        reason_code="INTERRUPTED_NATIVE_INTERRUPT",
                        reason="最新 checkpoint 已提交原生用户交互，不能重新执行产生交互的 Node。",
                        terminal_status=DurableExecutionStatus.AWAITING_USER,
                    )
                if not next_nodes or all(
                    node.strip().lower() in {"end", "__end__"}
                    for node in next_nodes
                ):
                    terminal_status = durable_execution_status(
                        result=values,
                        summary=(
                            values.get("summary")
                            if isinstance(values.get("summary"), dict)
                            else {}
                        ),
                    )
                    reason_suffix = terminal_status.value.upper()
                    return InterruptedTargetResolution(
                        kind="terminal",
                        snapshot=snapshot,
                        reentry_plan=None,
                        reason_code=(
                            "INTERRUPTED_GRAPH_COMPLETED"
                            if terminal_status is DurableExecutionStatus.COMPLETED
                            else f"INTERRUPTED_GRAPH_{reason_suffix}"
                        ),
                        reason=(
                            "最新 source-owned checkpoint 已到达 Graph terminal，"
                            f"其 durable status 为 {terminal_status.value}。"
                        ),
                        terminal_status=terminal_status,
                    )
                if len(next_nodes) != 1 or not next_nodes[0].strip():
                    return _interrupted_needs_attention(
                        "INTERRUPTED_CHECKPOINT_AMBIGUOUS",
                        "最新 checkpoint 包含无法安全重入的多个或空 Node。",
                        snapshot=snapshot,
                    )
                try:
                    boundary = await _persist_boundary(
                        workspace=workspace,
                        source=source,
                        target_node=next_nodes[0],
                        checkpoint_id=checkpoint_id,
                        checkpoint_ns=checkpoint_ns,
                        values=values,
                    )
                except Exception as exc:
                    return _interrupted_needs_attention(
                        "INTERRUPTED_BOUNDARY_PERSIST_FAILED",
                        "最新 checkpoint 的 Node Entry index 无法持久化。",
                        snapshot=snapshot,
                        cause=exc,
                    )
                lifecycle = load_application_lifecycle(workspace)
                plan = WorkflowReentryPlan(
                    reason=WorkflowReentryReason.INTERRUPTED_CONTINUE,
                    execution_kind=source.execution_kind,
                    target_node=next_nodes[0],
                    thread_id=source.thread_id,
                    source_run_id=source.run_id,
                    context_authority=WorkflowReentryContextAuthority(
                        kind=WorkflowReentryContextAuthorityKind.CHECKPOINT,
                        boundary_id=boundary.boundary_id,
                        source_run_id=boundary.source_run_id,
                        thread_id=boundary.thread_id,
                        target_node=boundary.target_node,
                        checkpoint_id=boundary.checkpoint_id,
                        checkpoint_ns=boundary.checkpoint_ns,
                    ),
                    lifecycle_authority=WorkflowReentryLifecycleAuthority(
                        owner_run_id=source.run_id,
                        revision=lifecycle.revision if lifecycle is not None else None,
                    ),
                    lineage_parent_run_id=source.run_id,
                )
                return InterruptedTargetResolution(
                    kind="continue",
                    snapshot=snapshot,
                    reentry_plan=plan,
                    reason_code="INTERRUPTED_CONTINUE_READY",
                    reason="已验证最新 source-owned checkpoint，可以从其唯一 pending Node 继续。",
                )
        except Exception as exc:
            return _interrupted_needs_attention(
                "INTERRUPTED_CHECKPOINT_READ_FAILED",
                "无法读取最新 source-owned committed checkpoint。",
                cause=exc,
            )
        return _interrupted_needs_attention(
            "INTERRUPTED_CHECKPOINT_AUTHORITY_MISSING",
            "没有找到最新 source-owned root checkpoint。",
        )


class RevisionTargetResolver:
    """把 Revision Coordinator 已确定的 target 与语义 State 固化为统一重入计划。"""

    def resolve(
        self,
        *,
        execution_kind: str,
        target_node: str,
        thread_id: str,
        run_id: str,
        semantic_state: dict[str, Any],
        lifecycle_revision: int | None,
    ) -> WorkflowReentryPlan:
        """对已构造的 Revision Context 求摘要，不重新运行 ChangeImpactAnalyzer。"""

        normalized_target = str(target_node or "").strip()
        if not normalized_target:
            raise RecoveryExecutionError(
                "REVISION_TARGET_MISSING",
                "Formal Revision 缺少 Backend 已确定的重入节点。",
            )
        return WorkflowReentryPlan(
            reason=WorkflowReentryReason.REVISION,
            execution_kind=execution_kind,  # type: ignore[arg-type]
            target_node=normalized_target,
            thread_id=thread_id,
            context_authority=WorkflowReentryContextAuthority(
                kind=WorkflowReentryContextAuthorityKind.REVISION_CONTEXT,
                revision_context_sha256=semantic_context_sha256(semantic_state),
            ),
            lifecycle_authority=WorkflowReentryLifecycleAuthority(
                owner_run_id=run_id,
                revision=lifecycle_revision,
            ),
        )


async def resolve_node_entry_boundary(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    target_node: str,
    graph: Any,
) -> NodeEntryBoundary:
    """从 committed checkpoint history 恢复精确 Node Entry authority 并重建旁路索引。"""

    indexed = await get_node_entry_boundary(
        workspace,
        source_run_id=source.run_id,
        thread_id=source.thread_id,
        target_node=target_node,
    )
    if indexed is not None:
        snapshot = await _read_boundary_snapshot(graph=graph, boundary=indexed)
        if _snapshot_matches_boundary(snapshot, boundary=indexed, source=source):
            return indexed
        # 旁路表只保存索引，不能把损坏的索引升级成 State authority；继续从
        # 同一 source/thread/target 的 committed history 精确重建，找不到才 fail closed。

    history_reader = getattr(graph, "aget_state_history", None)
    if not callable(history_reader):
        raise RecoveryExecutionError(
            "NODE_ENTRY_AUTHORITY_MISSING",
            "当前 production Graph 无法读取 committed checkpoint history。",
        )
    try:
        async for snapshot in history_reader(
            {"configurable": {"thread_id": source.thread_id, "checkpoint_ns": ""}}
        ):
            identity = _snapshot_identity(snapshot)
            if identity is None:
                continue
            thread_id, checkpoint_ns, checkpoint_id = identity
            values = getattr(snapshot, "values", {})
            values = values if isinstance(values, dict) else {}
            next_nodes = [str(node) for node in (getattr(snapshot, "next", ()) or ())]
            if (
                thread_id != source.thread_id
                or checkpoint_ns != ""
                or next_nodes != [target_node]
                or str(values.get("active_run_id") or "") != source.run_id
            ):
                continue
            return await _persist_boundary(
                workspace=workspace,
                source=source,
                target_node=target_node,
                checkpoint_id=checkpoint_id,
                checkpoint_ns=checkpoint_ns,
                values=values,
            )
    except RecoveryExecutionError:
        raise
    except Exception as exc:
        raise RecoveryExecutionError(
            "NODE_ENTRY_AUTHORITY_INVALID",
            "无法读取失败 Node 之前的 committed checkpoint history。",
        ) from exc
    raise RecoveryExecutionError(
        "NODE_ENTRY_AUTHORITY_MISSING",
        "没有找到 source run 中 next 精确等于失败 Node 的 committed checkpoint。",
    )


async def resolve_current_node_entry_boundary(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    graph: Any,
) -> NodeEntryBoundary:
    """从最新 committed history 确定异常逃出时实际待执行的业务 Node。"""

    history_reader = getattr(graph, "aget_state_history", None)
    if not callable(history_reader):
        raise RecoveryExecutionError(
            "NODE_ENTRY_AUTHORITY_MISSING",
            "当前 production Graph 无法读取 committed checkpoint history。",
        )
    try:
        async for snapshot in history_reader(
            {"configurable": {"thread_id": source.thread_id, "checkpoint_ns": ""}}
        ):
            identity = _snapshot_identity(snapshot)
            if identity is None:
                continue
            thread_id, checkpoint_ns, checkpoint_id = identity
            values = getattr(snapshot, "values", {})
            values = values if isinstance(values, dict) else {}
            next_nodes = [str(node) for node in (getattr(snapshot, "next", ()) or ())]
            if (
                thread_id != source.thread_id
                or checkpoint_ns != ""
                or len(next_nodes) != 1
                or next_nodes[0] in {SYNTHETIC_WORKFLOW_ENTRY_NODE, "__end__"}
                or str(values.get("active_run_id") or "") != source.run_id
            ):
                continue
            return await _persist_boundary(
                workspace=workspace,
                source=source,
                target_node=next_nodes[0],
                checkpoint_id=checkpoint_id,
                checkpoint_ns=checkpoint_ns,
                values=values,
            )
    except RecoveryExecutionError:
        raise
    except Exception as exc:
        raise RecoveryExecutionError(
            "NODE_ENTRY_AUTHORITY_INVALID",
            "无法从 committed checkpoint history 确定当前业务 Node。",
        ) from exc
    raise RecoveryExecutionError(
        "NODE_ENTRY_AUTHORITY_MISSING",
        "没有找到 source run 中唯一指向当前业务 Node 的 committed checkpoint。",
    )


def recovery_plan_from_reentry(plan: WorkflowReentryPlan) -> RecoveryPlan:
    """把 checkpoint re-entry contract 投影到既有 durable claim/fork 内部合同。"""

    authority = plan.context_authority
    if (
        plan.reason
        not in {
            WorkflowReentryReason.FAILURE_RETRY,
            WorkflowReentryReason.INTERRUPTED_CONTINUE,
        }
        or authority.kind is not WorkflowReentryContextAuthorityKind.CHECKPOINT
        or not plan.source_run_id
        or not authority.boundary_id
        or not authority.checkpoint_id
        or authority.checkpoint_ns != ""
    ):
        raise RecoveryExecutionError(
            "WORKFLOW_REENTRY_PLAN_INVALID",
            "当前计划不是可执行的 failure retry checkpoint 计划。",
        )
    return RecoveryPlan(
        source_run_id=plan.source_run_id,
        thread_id=plan.thread_id,
        decision=RecoveryDecision.READY_NATIVE,
        strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
        lifecycle_ownership_mode=RecoveryLifecycleOwnershipMode.SOURCE_OWNED,
        recovery_point_id=authority.boundary_id,
        checkpoint_id=authority.checkpoint_id,
        checkpoint_ns=authority.checkpoint_ns,
        next_nodes=[plan.target_node],
        reason_code=(
            "FAILED_NODE_REENTRY_READY"
            if plan.reason is WorkflowReentryReason.FAILURE_RETRY
            else "INTERRUPTED_CONTINUE_READY"
        ),
        reason=(
            "已验证失败 Node 之前的精确 Semantic Context checkpoint。"
            if plan.reason is WorkflowReentryReason.FAILURE_RETRY
            else "已验证最新 source-owned checkpoint 的精确 Node Entry。"
        ),
        lifecycle_revision=plan.lifecycle_authority.revision,
    )


def semantic_context_sha256(state: dict[str, Any]) -> str:
    """排除可重新绑定的运行元数据后，为 Revision Semantic Context 生成稳定摘要。"""

    semantic = {key: value for key, value in state.items() if key not in _RUNTIME_BINDING_KEYS}
    encoded = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _persist_boundary(
    *,
    workspace: str,
    source: DurableExecutionRecord,
    target_node: str,
    checkpoint_id: str,
    checkpoint_ns: str,
    values: dict[str, Any],
) -> NodeEntryBoundary:
    """同时写入新 Boundary 索引与旧 interrupted 路径仍消费的轻量 RecoveryPoint。"""

    lifecycle = load_application_lifecycle(workspace)
    captured_at = datetime.now(timezone.utc)
    point = await insert_recovery_point(
        workspace=workspace,
        point=RecoveryPoint(
            recovery_point_id=f"node-entry-{uuid4().hex}",
            run_id=source.run_id,
            thread_id=source.thread_id,
            kind=RecoveryPointKind.CHECKPOINT,
            checkpoint_id=checkpoint_id,
            checkpoint_ns=checkpoint_ns,
            graph_node=target_node,
            completed_node=None,
            next_nodes=[target_node],
            phase=str(values.get("phase") or target_node),
            state_status=str(values.get("status") or "running"),
            lifecycle_revision=lifecycle.revision if lifecycle is not None else None,
            workspace_revision=_optional_text(values.get("workspace_revision")),
            workspace_snapshot_hash=_optional_text(values.get("workspace_snapshot_hash")),
            captured_at=captured_at,
        ),
    )
    return await insert_node_entry_boundary(
        workspace=workspace,
        boundary=NodeEntryBoundary(
            boundary_id=point.recovery_point_id,
            source_run_id=source.run_id,
            thread_id=source.thread_id,
            target_node=target_node,
            checkpoint_id=checkpoint_id,
            checkpoint_ns=checkpoint_ns,
            lifecycle_revision=point.lifecycle_revision,
            workspace_revision=point.workspace_revision,
            workspace_snapshot_hash=point.workspace_snapshot_hash,
            captured_at=point.captured_at,
        ),
    )


async def _read_boundary_snapshot(*, graph: Any, boundary: NodeEntryBoundary) -> Any | None:
    """按完整 checkpoint identity 回读已有 Boundary 的真实 StateSnapshot。"""

    if not hasattr(graph, "aget_state"):
        return None
    try:
        return await graph.aget_state(
            {
                "configurable": {
                    "thread_id": boundary.thread_id,
                    "checkpoint_ns": boundary.checkpoint_ns,
                    "checkpoint_id": boundary.checkpoint_id,
                }
            }
        )
    except Exception:
        return None


def _snapshot_matches_boundary(
    snapshot: Any,
    *,
    boundary: NodeEntryBoundary,
    source: DurableExecutionRecord,
) -> bool:
    """验证 checkpoint identity、successor 与 source run semantic ownership。"""

    identity = _snapshot_identity(snapshot)
    values = getattr(snapshot, "values", {}) if snapshot is not None else {}
    values = values if isinstance(values, dict) else {}
    return bool(
        identity
        == (boundary.thread_id, boundary.checkpoint_ns, boundary.checkpoint_id)
        and [str(node) for node in (getattr(snapshot, "next", ()) or ())]
        == [boundary.target_node]
        and str(values.get("active_run_id") or "") == source.run_id
    )


def _snapshot_identity(snapshot: Any) -> tuple[str, str, str] | None:
    """从 LangGraph StateSnapshot 提取完整 checkpoint identity。"""

    config = getattr(snapshot, "config", None)
    configurable = config.get("configurable") if isinstance(config, dict) else None
    if not isinstance(configurable, dict):
        return None
    thread_id = _optional_text(configurable.get("thread_id"))
    checkpoint_id = _optional_text(configurable.get("checkpoint_id"))
    if not thread_id or not checkpoint_id:
        return None
    return thread_id, str(configurable.get("checkpoint_ns") or ""), checkpoint_id


def _snapshot_config(snapshot: Any) -> dict[str, Any] | None:
    """读取 StateSnapshot 的 configurable 配置，供最新 checkpoint 筛选。"""

    config = getattr(snapshot, "config", None)
    configurable = config.get("configurable") if isinstance(config, dict) else None
    return configurable if isinstance(configurable, dict) else None


def _snapshot_has_native_interrupt(snapshot: Any) -> bool:
    """只识别 LangGraph 已提交的真实 interrupt，不推断业务阶段。"""

    return any(
        bool(getattr(task, "interrupts", ()) or ())
        for task in (getattr(snapshot, "tasks", ()) or ())
    )


def _interrupted_needs_attention(
    reason_code: str,
    reason: str,
    *,
    snapshot: Any | None = None,
    cause: Exception | None = None,
) -> InterruptedTargetResolution:
    """构造 INTERRUPTED 的 fail-closed 解析结果，并保留原始异常供日志使用。"""

    del cause
    return InterruptedTargetResolution(
        kind="needs_attention",
        snapshot=snapshot,
        reentry_plan=None,
        reason_code=reason_code,
        reason=reason,
    )


def _optional_text(value: Any) -> str | None:
    """把可选值规范化为非空文本。"""

    normalized = str(value or "").strip()
    return normalized or None


__all__ = [
    "FailureTargetResolver",
    "InterruptedTargetResolution",
    "InterruptedTargetResolver",
    "RevisionTargetResolver",
    "SYNTHETIC_WORKFLOW_ENTRY_NODE",
    "recovery_plan_from_reentry",
    "resolve_current_node_entry_boundary",
    "resolve_node_entry_boundary",
    "semantic_context_sha256",
    "workflow_entry",
]
