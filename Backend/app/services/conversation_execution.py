"""将二次修改运行接入现有 Workbench lifecycle、DurableExecution 和节点恢复观察。"""

from __future__ import annotations

import asyncio
from typing import Any
from app.config import execution_recovery_heartbeat_seconds, execution_recovery_lease_ttl_seconds
from app.services.backend_instance import current_backend_instance

from app.domain.execution_recovery import DurableExecutionStatus, RecoveryExecutionError
from app.persistence.execution_recovery import get_execution
from app.services.workflow_reentry import resolve_current_node_entry_boundary
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError, _application_lifecycle_lock, application_lifecycle_path,
    application_lifecycle_payload, complete_workbench_execution,
    load_application_lifecycle, start_workbench_execution, write_application_lifecycle,
)
from app.protocols.workflow.lifecycle import (
    fail_workflow_lifecycle, project_workflow_lifecycle_boundary, stop_workflow_lifecycle,
)
from app.services.execution_recovery import (
    assert_run_id_available, durable_execution_status, observe_execution_cancelled,
    observe_execution_failed, observe_execution_finished, observe_execution_started,
    observe_node_started,
)
from app.services.execution_lease_heartbeat import maintain_execution_heartbeat, stop_execution_heartbeat
from app.services.workspace_process_registry import workspace_process_registry


class ConversationExecution:
    """仅适配原恢复机制的运行边界，不创建第二套恢复存储或动作。"""

    def __init__(self, workspace: str, thread_id: str, run_id: str, graph: Any, context: Any = None):
        """绑定当前 AG-UI 身份或统一执行器已经准备好的 Native child。"""

        self.workspace, self.thread_id, self.run_id = workspace, thread_id, run_id
        self.context = context
        self.graph = graph
        self.registered = context is not None
        self.node = context.recovery_plan.target_node if context else "scan_workspace_code"
        self.heartbeat = context.heartbeat_task if context else None

    def arguments(self) -> dict[str, Any]:
        """向共享观察函数提供同一份不可变运行身份。"""

        return dict(workspace=self.workspace, run_id=self.run_id,
                    thread_id=self.thread_id, workflow_scope="conversation")

    async def start(self, state: dict[str, Any], snapshot: Any, owner: str | None) -> None:
        """正常请求登记 Workbench；Native child 沿用已经验证的 claim 和 handoff。"""

        if self.context is not None:
            state["lifecycle"] = self.context.lifecycle_payload
            return
        lifecycle = load_application_lifecycle(self.workspace)
        if lifecycle is None:
            return
        await assert_run_id_available(self.workspace, self.run_id)
        values = getattr(snapshot, "values", {}) or {}
        previous = lifecycle.active_executions.get(str(values.get("active_run_id") or ""))
        replaces = None
        if previous is not None:
            # 未终止的请求只能走统一恢复，不能以重新发送覆盖中断事实或绕过确认门。
            if previous.thread_id != self.thread_id or previous.owner_session_id != owner:
                raise ValueError("二次修改运行不属于当前会话。")
            if previous.status.value != "awaiting_user" or getattr(snapshot, "next", ()):
                raise ValueError("当前二次修改尚未结束，请使用底部统一恢复入口。")
            replaces = previous.run_id
        self.node = str(state.get("direct_modification_resume_node") or "scan_workspace_code")
        lifecycle = start_workbench_execution(
            self.workspace, scope="application", target_id="conversation", page_id=None,
            thread_id=self.thread_id, run_id=self.run_id, phase=self.node,
            owner_session_id=owner, replaces_run_id=replaces,
        )
        state["lifecycle"] = application_lifecycle_payload(lifecycle)
        await observe_execution_started(**self.arguments(), project_id=None,
                                        first_node=self.node, owner_session_id=owner)
        self.registered = True
        self.heartbeat = asyncio.create_task(maintain_execution_heartbeat(
            workspace=self.workspace, run_id=self.run_id,
            backend_instance_id=current_backend_instance().instance_id,
            interval_seconds=execution_recovery_heartbeat_seconds(),
            lease_ttl_seconds=execution_recovery_lease_ttl_seconds(),
        ))

    async def started_node(self, node: str) -> None:
        """记录真实节点入口，失败解析仍使用共享的 LangGraph history resolver。"""

        self.node = node
        if self.registered:
            await observe_node_started(**self.arguments(), node_name=node)

    async def finish(self, state: dict[str, Any]) -> None:
        """沿用共享状态解释和确认投影，完成时释放本次 execution 的资源。"""

        if not self.registered:
            return
        status = durable_execution_status(result=state, summary={})
        clarification = state.get("clarification") or {}
        formal_handoff = status is DurableExecutionStatus.AWAITING_USER and clarification.get("mode") == "revision_impact_confirmation"
        if formal_handoff:
            lifecycle = self.finish_formal_handoff()
            state["lifecycle"] = application_lifecycle_payload(lifecycle)
        elif status is DurableExecutionStatus.COMPLETED:
            # 正式修订的 impact 由原 lifecycle 持有；来源对话不继续占用 Workbench 资源。
            lifecycle = complete_workbench_execution(self.workspace, run_id=self.run_id,
                                                     phase=self.node)
            state["lifecycle"] = application_lifecycle_payload(lifecycle)
        else:
            state["lifecycle"] = project_workflow_lifecycle_boundary(
                self.workspace, run_id=self.run_id, node_name=self.node, update=state)
        await observe_execution_finished(**self.arguments(), status=status)

    def finish_formal_handoff(self):
        """在同一锁内释放来源执行并刷新自身 impact 绑定，不接受已发生的事实漂移。"""

        with _application_lifecycle_lock(application_lifecycle_path(self.workspace)):
            current = load_application_lifecycle(self.workspace)
            pending = current.pending_revision_impact if current else None
            if (pending is None or pending.source_run_id != self.run_id
                    or pending.source_thread_id != self.thread_id
                    or pending.based_on_lifecycle_revision != current.revision):
                raise ApplicationLifecycleConflictError("二次修改交接的影响范围已失效。")
            completed = complete_workbench_execution(self.workspace, run_id=self.run_id, phase=self.node)
            # 只刷新本事务释放来源执行产生的 revision；外部变更仍由原确认校验拒绝。
            revision = completed.revision + 1
            updated = completed.model_copy(update={"revision": revision,
                "pending_revision_impact": pending.model_copy(update={"based_on_lifecycle_revision": revision})})
            return write_application_lifecycle(self.workspace, updated, expected_revision=completed.revision)

    async def failed(self, error: BaseException) -> None:
        """中断和异常沿用同一 Durable 状态及错误分类，不自动重新派发节点。"""

        if not self.registered:
            return
        if isinstance(error, asyncio.CancelledError):
            await observe_execution_cancelled(**self.arguments(), explicitly_cancelled=
                workspace_process_registry.is_run_cancelled(self.run_id))
            stop_workflow_lifecycle(self.workspace, run_id=self.run_id, phase=self.node)
        else:
            source = await get_execution(self.workspace, self.run_id)
            if source is not None:
                try:
                    boundary = await resolve_current_node_entry_boundary(
                        workspace=self.workspace, source=source, graph=self.graph)
                    self.node = boundary.target_node
                except RecoveryExecutionError:
                    # 缺少精确入口时只保存失败事实，统一 resolver 会拒绝签发恢复动作。
                    pass
            await observe_execution_failed(**self.arguments(), exception=error, authoritative_node=self.node)
            fail_workflow_lifecycle(self.workspace, run_id=self.run_id, phase=self.node, error=error)

    async def close(self) -> None:
        """释放当前执行的共享心跳，不触碰其他运行。"""

        await stop_execution_heartbeat(self.heartbeat)
