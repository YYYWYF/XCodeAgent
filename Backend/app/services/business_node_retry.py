"""从完整失败现场读取业务上下文，以最新磁盘输入重新执行原节点。"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app.domain.execution_recovery import (
    DurableExecutionRecord, DurableExecutionStatus, RecoveryAction, RecoveryActionKind,
    RecoveryActionPlan, RecoveryExecutionError, RecoveryIncidentStatus,
)

NO_RETRY_ENTRY = "找不到恢复入口，请手动发起流程。"
_locks: dict[tuple[str, str], asyncio.Lock] = {}


@dataclass(frozen=True)
class RetryHint:
    """只保留 checkpoint 提供的节点和原始用户要求。"""

    node: str
    request: str


async def read_retry_hint(source: DurableExecutionRecord, graph: Any) -> RetryHint | None:
    """有界读取失败入口，读取故障保留原入口，不能当作节点缺失处理。"""
    from app.services.retry_preparation import await_checkpoint_preparation
    try:
        return await await_checkpoint_preparation(_read_retry_hint(source, graph), source.run_id)
    except TimeoutError as exc:
        raise RecoveryExecutionError("RETRY_CHECKPOINT_UNAVAILABLE",
            "暂时无法读取执行进度，请稍后重试。原恢复入口已保留。") from exc


async def _read_retry_hint(source: DurableExecutionRecord, graph: Any) -> RetryHint | None:
    """读取本 Run 的已提交记录定位节点，与实际执行共用同一入口解释。"""
    from app.services.checkpoint_execution import read_checkpoint_execution
    execution = await read_checkpoint_execution(source, graph)
    return _execution_retry_hint(execution, graph)


def _execution_retry_hint(execution, graph) -> RetryHint | None:
    """确认处理失败时重新展示原审阅门，其他业务节点保持原失败位置。"""
    if execution is not None:
        node = "technical_planning_review" if execution.node == "technical_planning_confirm" else execution.node
        if node in graph.nodes and node not in {"workflow_entry", "handle_failure", "await_user_input", "__end__"}:
            return RetryHint(node=node, request=execution.request)
    return None


async def retry_action_plan(source: DurableExecutionRecord, graph: Any) -> RecoveryActionPlan:
    """用同一轻量节点定位为 Planning 和 Workbench 签发重试或可关闭提示。"""
    hint = await read_retry_hint(source, graph)
    from app.services.stage_prerequisites import regeneration_for_retry
    issue = regeneration_for_retry(source.workspace, hint.node, graph) if hint else None
    target = issue.node if issue else hint.node if hint else ""
    digest = hashlib.sha256(f"{source.run_id}:{source.updated_at}:{target}".encode()).hexdigest()
    action = RecoveryAction(actionId=f"retry:{digest}", kind=RecoveryActionKind.RETRY_BUSINESS_NODE,
                            targetNode=target, label=issue.label if issue else (
                                "继续生成" if source.execution_kind == "application_planning"
                                and source.status is DurableExecutionStatus.INTERRUPTED else "重试"),
                            description=issue.message if issue else "读取当前文件，重新执行失败的业务步骤。",
                            requiresConfirmation=False) if hint else None
    return RecoveryActionPlan(incidentId=digest, sourceRunId=source.run_id, threadId=source.thread_id,
                              executionKind=source.execution_kind,
                              status=RecoveryIncidentStatus.RECOVERABLE if hint else RecoveryIncidentStatus.NEEDS_ATTENTION,
                              reasonCode="STAGE_PREREQUISITE_INCOMPLETE" if issue else "BUSINESS_NODE_RETRY_READY" if hint else "RETRY_ENTRY_NOT_FOUND",
                              message=issue.message if issue else "读取当前文件，重新执行失败的业务步骤。" if hint else NO_RETRY_ENTRY,
                              primaryAction=action, updatedAt=source.updated_at)


async def release_unretryable_execution(source: DurableExecutionRecord) -> Any:
    """等待旧工作真实退出，只释放该失败执行，绝不清理其他会话或磁盘产物。"""
    if source.status not in {DurableExecutionStatus.FAILED, DurableExecutionStatus.INTERRUPTED} and not _stopped_workbench_source(source):
        raise RecoveryExecutionError("RETRY_SOURCE_ACTIVE", "仍在运行或等待确认的执行不能按失败任务释放。")
    from app.services.failed_run_cleanup import require_failed_run_drained
    from app.services.application_lifecycle import (
        _application_lifecycle_lock, application_lifecycle_path, end_workbench_execution,
        load_application_lifecycle, write_application_lifecycle, utc_now, execution_belongs_to_active_revision,
    )
    await require_failed_run_drained(source.run_id)
    from app.workspace.run_lease import workspace_run_leases
    workspace_run_leases.release_run(workspace_root=source.workspace, run_id=source.run_id)
    if load_application_lifecycle(source.workspace) is None:
        return None
    if source.execution_kind == "workbench":
        return end_workbench_execution(source.workspace, run_id=source.run_id, missing_ok=True)
    with _application_lifecycle_lock(application_lifecycle_path(source.workspace)):
        current = load_application_lifecycle(source.workspace)
        if current is None or current.active_run_id not in {None, source.run_id}:
            return current
        active = current.active_formal_revision
        updates = {}
        if current.active_run_id == source.run_id:
            updates["active_run_id"] = None
        if (active is not None and active.planning_run_id == source.run_id
                and active.planning_thread_id == source.thread_id
                and not any(execution_belongs_to_active_revision(current, execution)
                            for execution in current.active_executions.values())):
            # 无业务入口的修订已无法继续，释放其唯一占用；文件和其他会话保持原样。
            updates["active_formal_revision"] = None
        # 正式返回设计只暂时借用 initialization 展示进度；放弃无入口的修订时，
        # 必须一起恢复应用早已完成首次创建的事实，不能遗留“分析中”阻断新会话。
        # durable 的 design_intent_analysis 也能识别已释放修订、尚未收齐投影的同次收尾。
        released_design = (
            updates.get("active_formal_revision", active) is None
            and current.initialization.thread_id == source.thread_id
            and not current.active_executions
            and (active is not None or source.first_node == "design_intent_analysis")
        )
        if released_design:
            from app.domain.application_lifecycle import (
                ApplicationLifecycleStage, ApplicationLifecycleStatus,
            )
            initialization = current.initialization.model_copy(update={
                "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                "status": ApplicationLifecycleStatus.COMPLETED,
            })
            if initialization != current.initialization:
                updates.update(initialization=initialization, error=None)
        if not updates:
            return current
        return write_application_lifecycle(source.workspace, current.model_copy(update={
            **updates, "updated_at": utc_now(), "revision": current.revision + 1,
        }), expected_revision=current.revision)


def _stopped_workbench_source(source: DurableExecutionRecord) -> bool:
    """只有当前生命周期仍持有的同 Run/Thread/Owner 暂停执行可以主动继续。"""
    if source.status is not DurableExecutionStatus.CANCELLED or source.execution_kind != "workbench":
        return False
    from app.services.application_lifecycle import load_application_lifecycle
    lifecycle = load_application_lifecycle(source.workspace)
    execution = lifecycle.active_executions.get(source.run_id) if lifecycle else None
    return bool(execution and execution.status == "stopped" and execution.thread_id == source.thread_id
                and execution.owner_session_id == source.owner_session_id)


async def prepare_business_node_retry(source: DurableExecutionRecord, graph: Any, *, allow_stopped: bool = False):
    """读取完整失败现场、刷新磁盘输入，再串行交接运行归属并重新执行一次。"""
    if source.status not in {DurableExecutionStatus.FAILED, DurableExecutionStatus.INTERRUPTED} and not (allow_stopped and _stopped_workbench_source(source)):
        raise RecoveryExecutionError("RETRY_SOURCE_ACTIVE", "当前执行不是失败或中断状态。")
    from app.services.application_lifecycle import (
        application_lifecycle_payload, load_application_lifecycle,
        handoff_application_planning_run_for_recovery, handoff_workbench_execution_for_recovery,
        claim_application_planning_run_for_recovery,
    )
    from app.services.execution_recovery import observe_execution_started
    from app.services.execution_recovery_executor import NativeRecoveryRuntimeContext, _acquire_workspace_lease, _recovery_observability
    from app.services.failed_run_cleanup import require_failed_run_drained
    from app.services.checkpoint_execution import read_checkpoint_execution
    from app.services.retry_node_state import retry_node_state, refresh_retry_node_inputs

    async with _locks.setdefault((source.workspace, source.run_id), asyncio.Lock()):
        lifecycle = load_application_lifecycle(source.workspace)
        if source.execution_kind == "application_planning" and lifecycle and lifecycle.active_run_id not in {None, source.run_id}:
            raise RecoveryExecutionError("RETRY_SOURCE_SUPERSEDED", "原任务已被新的执行替代，请查看当前流程。")
        # 节点和完整输入必须来自同一份已提交现场，不拼接其他 Run 的历史状态。
        checkpoint = await read_checkpoint_execution(source, graph)
        hint = _execution_retry_hint(checkpoint, graph)
        if hint is None:
            await release_unretryable_execution(source)
            raise RecoveryExecutionError("RETRY_ENTRY_NOT_FOUND", NO_RETRY_ENTRY)
        await require_failed_run_drained(source.run_id)
        from app.services.failed_execution_reconciliation import reconcile_failed_execution_lifecycle
        reconcile_failed_execution_lifecycle(source)
        lifecycle = load_application_lifecycle(source.workspace)
        if source.execution_kind == "application_planning" and lifecycle and lifecycle.active_run_id not in {None, source.run_id}:
            raise RecoveryExecutionError("RETRY_SOURCE_SUPERSEDED", "原任务已被新的执行替代，请查看当前流程。")
        execution = lifecycle.active_executions.get(source.run_id) if lifecycle else None
        node = hint.node
        from app.services.stage_prerequisites import regeneration_for_retry
        issue = regeneration_for_retry(source.workspace, node, graph)
        if issue:
            node = issue.node
        planning_handoff = node not in graph.nodes and node in {"requirements", "product_planning", "ui_confirmation", "technical_planning_begin"}
        if planning_handoff:
            from app.graph.application_planning_workflow import application_planning_graph_for_request
            graph = await application_planning_graph_for_request(workspace=source.workspace, project_id=source.project_id)
        state = retry_node_state(checkpoint.snapshot.values, node,
                                 planning=source.execution_kind == "application_planning" or planning_handoff)
        state.update({"workspace": source.workspace, "project_id": source.project_id,
                                 "request": hint.request, "resume_from": node,
                                 "active_thread_id": source.thread_id,
                                 "retry_source_run_id": source.run_id,
                                 "owner_session_id": source.owner_session_id or "",
                                 "workflow_scope": source.workflow_scope or ""})
        if execution:
            scope = {"type": execution.scope, "targetId": execution.target_id}
            if execution.development_target and execution.development_target.api_contract_id:
                scope["apiContractId"] = execution.development_target.api_contract_id
            state.update(build_execution_scope=scope, selectedPageId=execution.page_id or "")
        if lifecycle and lifecycle.active_formal_revision:
            revision = lifecycle.active_formal_revision
            state.update(change_id=revision.change_id, change_target=revision.target.model_dump(mode="json", by_alias=True))
            if source.execution_kind == "application_planning" and revision.planning_thread_id == source.thread_id:
                generation_target = "technical_planning" if node.startswith("technical_planning_") else node
                state.update(design_change_submission=True, design_change_request=hint.request,
                    design_change_target=generation_target, design_change_generation_target=generation_target,
                    design_change_generation_request=hint.request)
        if node == "template_reconcile":
            state["workflow_action"] = "retry_template_reconcile"
        if planning_handoff:
            state["workflow_scope"] = "application_planning"
            generation_target = "technical_planning" if node.startswith("technical_planning_") else node
            state.update(design_change_submission=True, design_change_request=hint.request,
                design_change_target=generation_target, design_change_generation_target=generation_target,
                design_change_generation_request=hint.request)
        refresh_retry_node_inputs(state, node)
        if source.workflow_scope == "conversation":
            # 从原失败节点继续，分类、授权、变更集和返修证据均保留。
            state["direct_modification_resume_node"] = node
        new_run = f"retry-{uuid4().hex}"
        observability = _recovery_observability(run_id=new_run, thread_id=source.thread_id,
            project_id=source.project_id, workspace=source.workspace)
        revision_number = lifecycle.revision if lifecycle else None
        before_handoff = lifecycle
        lease = None
        try:
            if planning_handoff:
                from app.services.artifact_regeneration_handoff import handoff_artifact_generation
                lifecycle = handoff_artifact_generation(source, new_run_id=new_run, node=node, request=hint.request)
                state.update(change_id=lifecycle.active_formal_revision.change_id,
                    change_target=lifecycle.active_formal_revision.target.model_dump(mode="json", by_alias=True))
            elif source.execution_kind == "workbench":
                lifecycle = handoff_workbench_execution_for_recovery(source.workspace, source_run_id=source.run_id,
                    new_run_id=new_run, thread_id=source.thread_id, phase=node, expected_lifecycle_revision=revision_number)
            elif lifecycle and lifecycle.active_run_id == source.run_id:
                lifecycle = handoff_application_planning_run_for_recovery(source.workspace, source_run_id=source.run_id,
                    new_run_id=new_run, thread_id=source.thread_id, expected_lifecycle_revision=revision_number)
            else:
                lifecycle = claim_application_planning_run_for_recovery(source.workspace, new_run_id=new_run,
                    thread_id=source.thread_id, expected_lifecycle_revision=revision_number)
            lease = None if planning_handoff else _acquire_workspace_lease(
                workspace=source.workspace, source=source, new_run_id=new_run, lifecycle=lifecycle)
            child = await observe_execution_started(workspace=source.workspace, project_id=source.project_id,
                    thread_id=source.thread_id, run_id=new_run, workflow_scope=state["workflow_scope"] or None,
                    first_node=node, owner_session_id=source.owner_session_id)
            state.update(active_run_id=new_run, active_thread_id=source.thread_id, observability=observability,
                         lifecycle=application_lifecycle_payload(lifecycle) if lifecycle else None)
            # 指定一个全新的 checkpoint identity，使 LangGraph 从 START 接受新输入；不读取旧 State。
            config = {
                "configurable": {"thread_id": source.thread_id, "checkpoint_ns": "", "checkpoint_id": str(uuid4())},
                # 嵌套 Agent 重建 Context 时仍能识别本轮取消身份，不能沿用来源 Run。
                "metadata": {"run_id": new_run, "thread_id": source.thread_id, "workspace": source.workspace,
                             "selected_skill_names": list(state.get("selected_skill_names") or [])},
            }
            return NativeRecoveryRuntimeContext(source_execution=source, child_execution=child,
                recovery_plan=SimpleNamespace(target_node=node), new_run_id=new_run, thread_id=source.thread_id,
                project_id=source.project_id, workspace=source.workspace, workflow_scope=state["workflow_scope"] or None,
                graph=graph, fork_config=config, observation_config={"configurable": {"thread_id": source.thread_id}},
                fork_snapshot=SimpleNamespace(values=state), lifecycle_payload=state["lifecycle"],
                workspace_lease=lease, observability=observability, heartbeat_task=None, restart_node=True,
                retry_lifecycle_before_handoff=before_handoff)
        except BaseException as exc:
            from app.services.retry_preparation import abandon_prepared_retry
            from app.services.failed_run_cleanup import shield_run_cleanup
            # 尚未执行业务时撤销交接，不能把原失败入口一起删除。
            await shield_run_cleanup(abandon_prepared_retry(workspace=source.workspace, source=source,
                before=before_handoff, new_run_id=new_run, lease=lease))
            if isinstance(exc, asyncio.CancelledError) or not isinstance(exc, Exception):
                raise
            raise RecoveryExecutionError("RETRY_PREPARATION_FAILED",
                "重试准备未完成，已保留原失败节点和提问，请重试。") from exc
