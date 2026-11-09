"""从当前文档建立新的本机审阅现场，复用原确认节点，绝不调用生成节点。"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.execution_recovery import DurableExecutionRecord, DurableExecutionStatus, RecoveryExecutionError
from app.persistence.execution_recovery import insert_execution
from app.services import application_lifecycle as lifecycle_service
from app.services.document_review_assets import find_document_review, document_review_is_current
from app.services.recovery_checkpoint import checkpoint_thread_exists

_locks: dict[str, asyncio.Lock] = {}


async def prepare_document_review(workspace: str, *, application_id: str, thread_id: str,
                                  session_id: str | None = None, planning: bool = True) -> bool:
    """串行建立或复用当前审阅，校验文件与真实执行后才登记新的 Run。"""
    workspace = str(Path(workspace).resolve())
    async with _locks.setdefault(workspace, asyncio.Lock()):
        from app.services.detached_execution import active_detached_count
        from app.services.ui_design_generation_pool import active_ui_design_count
        from app.protocols.workflow.run_control import workflow_run_registry
        from app.protocols.application_planning_run_lock import application_planning_run_lock
        if (workflow_run_registry.active_run_ids(workspace) or active_detached_count(workspace)
                or active_ui_design_count(workspace) or application_planning_run_lock(thread_id).locked()):
            raise RecoveryExecutionError("DOCUMENT_REVIEW_BUSY", "当前应用仍有真实任务，请等待其结束后重新同步。")
        before = lifecycle_service.load_application_lifecycle(workspace)
        if before is None or before.application.id != application_id:
            raise ValueError("文档审阅不属于当前应用。")
        from app.services.workspace_checkpoint_reconciliation import _run_is_live
        if any(_run_is_live(workspace, run_id) for run_id in before.active_executions):
            raise RecoveryExecutionError("DOCUMENT_REVIEW_BUSY", "原任务的后台工作尚未退出，请稍后重新同步。")
        from app.graph.application_planning_workflow import application_planning_graph_for_request
        from app.graph.workflow import workflow_graph_for_request
        factory = application_planning_graph_for_request if planning else workflow_graph_for_request
        graph = None
        if await checkpoint_thread_exists(workspace, thread_id):
            graph = await factory(workspace=workspace, project_id=application_id)
            saved = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            identity = saved.values.get("document_review")
            if not identity:
                return False
            if document_review_is_current(workspace, identity):
                from app.protocols.application_planning_interrupt import application_planning_interrupt_from_snapshot
                if planning and not application_planning_interrupt_from_snapshot(saved) and candidate_review_next(saved):
                    await graph.ainvoke(None, {"configurable": {"thread_id": thread_id}})
                return False
        if not planning:
            for execution in before.active_executions.values():
                if execution.thread_id != thread_id and await checkpoint_thread_exists(workspace, execution.thread_id):
                    raise RecoveryExecutionError("DOCUMENT_REVIEW_BUSY", "当前文档仍属于另一个本机审阅会话。")
        candidate = find_document_review(workspace, planning=planning)
        if candidate is None:
            return False
        identity = {"kind": candidate.kind, "digest": candidate.digest, "planning": planning}
        run_id = "document-review-" + uuid4().hex
        now = datetime.now(timezone.utc)
        state = {**candidate.state, "active_run_id": run_id, "active_thread_id": thread_id,
            "owner_session_id": session_id or "", "document_review": identity,
            "resume_from": candidate.node, "resume_execution_run_id": "", "retry_source_run_id": "",
            "application_planning_review_route": "", "revision_interaction": {}, "timeline": []}
        if graph is None:
            graph = await factory(workspace=workspace, project_id=application_id)
        # 校验读取期间未被外部修改；新的 checkpoint 只记录审阅上下文，不写文档正文。
        if not document_review_is_current(workspace, identity):
            raise RecoveryExecutionError("DOCUMENT_REVIEW_SOURCE_CHANGED", "文档已变化，请重新同步后审阅。")
        from app.services.preview_runtime_guard import maintenance_lock
        from app.workspace.task_documents import build_task_plan_lifecycle_lock
        with maintenance_lock, build_task_plan_lifecycle_lock(workspace), lifecycle_service._application_lifecycle_lock(lifecycle_service.application_lifecycle_path(workspace)):
            current = lifecycle_service.load_application_lifecycle(workspace)
            if current is None or current.revision != before.revision:
                raise RecoveryExecutionError("DOCUMENT_REVIEW_STATE_CHANGED", "应用状态已变化，请重新同步。")
            if planning:
                active = current.active_formal_revision
                updates = {"initialization": current.initialization.model_copy(update={
                    "stage": ApplicationLifecycleStage(candidate.stage), "status": ApplicationLifecycleStatus.AWAITING_USER,
                    "thread_id": thread_id}), "active_run_id": run_id, "error": None}
                if active:
                    # 保留正式修订分支自身的状态；设计修订后续修改仍由原设计规划上下文处理。
                    updates["active_formal_revision"] = active.model_copy(update={"planning_thread_id": thread_id,
                        "planning_run_id": run_id})
                lifecycle_service.write_application_lifecycle(workspace, current.model_copy(update={
                    **updates, "updated_at": lifecycle_service.utc_now(), "revision": current.revision + 1,
                }), expected_revision=current.revision)
            else:
                _prepare_workbench(candidate, state, workspace, thread_id, run_id, session_id)
                identity["digest"] = find_document_review(workspace, planning=False).digest
        await insert_execution(DurableExecutionRecord(run_id=run_id, thread_id=thread_id, workspace=workspace,
            project_id=application_id, owner_session_id=session_id,
            execution_kind="application_planning" if planning else "workbench",
            workflow_scope="application_planning" if planning else None, first_node=candidate.node,
            current_node=candidate.node, status=DurableExecutionStatus.AWAITING_USER, started_at=now, updated_at=now))
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
                  "metadata": {"workspace": workspace, "run_id": run_id, "thread_id": thread_id}}
        await graph.aupdate_state(config, state, as_node="workflow_entry" if planning else candidate.node)
        if planning:
            # 入口已由服务端固定为 review；只执行到原生 interrupt，生成和确认节点均不运行。
            await graph.ainvoke(None, config)
        return True


def candidate_review_next(snapshot) -> bool:
    """只允许继续建立被中断的审阅节点，不重放任何生成或确认节点。"""
    return bool(snapshot.next) and set(snapshot.next) <= {
        "requirement_document_review", "ui_confirmation_review", "technical_planning_review"}


def _prepare_workbench(candidate, state: dict, workspace: str, thread_id: str, run_id: str, session_id: str | None) -> None:
    """为现有工作台草稿登记本机交互身份，旧用户的确认凭据不继承。"""
    if not session_id:
        raise ValueError("工作台文档审阅需要当前会话身份。")
    if candidate.kind == "build_task_plan":
        _rebind_pending_dag(state, workspace, run_id, session_id)
    scope = state.get("build_execution_scope") or {"type": "application", "targetId": "application"}
    lifecycle_service.start_workbench_execution(workspace, scope=scope["type"], target_id=scope["targetId"],
        page_id=scope["targetId"] if scope["type"] == "page" else None,
        thread_id=thread_id, run_id=run_id, phase=candidate.node, owner_session_id=session_id)
    from app.protocols.workflow.lifecycle import _pending_interaction
    from app.domain.application_lifecycle import WorkbenchExecutionStatus
    pending_type, pending_payload = _pending_interaction(state)
    lifecycle_service.update_workbench_execution(workspace, run_id=run_id, phase=candidate.node,
        status=WorkbenchExecutionStatus.AWAITING_USER, pending_type=pending_type, pending_payload=pending_payload)


def _rebind_pending_dag(state: dict, workspace: str, run_id: str, session_id: str) -> None:
    """只重绑 Pending 的本机执行归属，保留已生成任务、规划身份及原输入摘要。"""
    from app.workspace.task_documents import write_pending_build_task_plan_atomic, load_pending_build_task_plan, validate_pending_self_digest
    from app.services.planning_refresh_recovery import _pending_confirmation
    pending = state["build_task_plan"]
    identity = validate_pending_self_digest(pending)
    body = {key: value for key, value in pending.items() if key not in {"draft_identity", "planning_provenance"}}
    write_pending_build_task_plan_atomic({"workspace": workspace}, body, owner_session_id=session_id,
        planning_run_id=identity.planning_run_id, workflow_run_id=run_id,
        base_confirmed_plan_digest=identity.base_confirmed_plan_digest, input_fingerprint=identity.input_fingerprint,
        build_execution_scope=identity.build_execution_scope, created_at=identity.created_at,
        planning_provenance=pending["planning_provenance"])
    pending = load_pending_build_task_plan({"workspace": workspace})
    state.update(build_task_plan=pending, clarification=_pending_confirmation(workspace, pending, validate_pending_self_digest(pending)))
