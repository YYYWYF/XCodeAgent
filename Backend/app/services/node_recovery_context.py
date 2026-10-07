"""把已验证的 Native Recovery 来源限定在本次目标节点调用。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.domain.execution_recovery import WorkflowReentryReason


@dataclass(slots=True)
class NodeRecoveryContext:
    """投影现有 RecoveryAttempt 与 checkpoint 身份，不独立授予恢复权限。"""

    source_run_id: str
    execution_run_id: str
    thread_id: str
    target_node: str
    checkpoint_id: str
    reentry_reason: WorkflowReentryReason
    build_execution_scope: dict[str, Any] = field(default_factory=dict)
    entry_state: dict[str, Any] = field(default_factory=dict)
    internal_progress: dict[str, Any] | None = None
    source_lineage_run_ids: tuple[str, ...] = ()
    claimed: bool = False


_runtime_context: ContextVar[NodeRecoveryContext | None] = ContextVar(
    "native_node_recovery_runtime", default=None
)
_node_context: ContextVar[NodeRecoveryContext | None] = ContextVar(
    "native_node_recovery_invocation", default=None
)


@contextmanager
def bind_recovery_runtime(context: NodeRecoveryContext | None) -> Iterator[None]:
    """仅在本次 Graph stream 的异步上下文中提供经服务端验证的来源。"""

    token = _runtime_context.set(context)
    try:
        yield
    finally:
        _runtime_context.reset(token)


@contextmanager
def bind_node_recovery(state: dict[str, Any], node_name: str) -> Iterator[None]:
    """仅对 child 的首次目标节点调用开放来源，节点返回或抛错即撤销。"""

    context = _runtime_context.get()
    selected = None
    if (
        context is not None
        and not context.claimed
        and context.target_node == node_name
        and str(state.get("active_run_id") or "") == context.execution_run_id
        and str(state.get("active_thread_id") or "") == context.thread_id
    ):
        context.claimed = True
        selected = context
    token = _node_context.set(selected)
    try:
        yield
    finally:
        _node_context.reset(token)


def current_node_recovery_context() -> NodeRecoveryContext | None:
    """只返回当前节点调用中已领取的恢复来源。"""

    return _node_context.get()
