"""确认后的单任务合同提升与新 Build Run 交接，原运行证据保持不可变。"""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

from app.domain.build_task_repair import TaskRepairProposal
from app.persistence.execution_recovery import get_execution
from app.services.build_task_repair import (
    repair_lock, load_repair_source, load_draft, save_draft, validate_draft,
)
from app.services.build_task_repair_contract import compile_repaired_plan
from app.workspace.task_documents import (
    build_task_plan_lifecycle_lock, build_task_plan_json_path, build_task_plan_sha256,
    load_build_task_plan_json, write_json_atomic, write_build_execution_record,
    write_build_run_task_plan_json,
)


def rollback_repair_plan(request, original: dict, committed_digest: str, draft: dict) -> None:
    """仅回退本次尚未启动的计划提升，不能覆盖后来确认的新计划。"""
    with build_task_plan_lifecycle_lock(request.workspaceRoot):
        path = build_task_plan_json_path({"workspace": request.workspaceRoot})
        if build_task_plan_sha256(load_build_task_plan_json(path)) == committed_digest:
            write_json_atomic(path, original)
            save_draft(request, draft)


async def prepare_repair_execution(request, run_id: str):
    """用户确认精确草稿后只重置当前任务，复用既有租约和 Workflow 执行流。"""
    from app.graph.subgraphs.build import _build_execution_record, _build_runtime_progress
    from app.services.application_lifecycle import (
        load_application_lifecycle, handoff_workbench_execution_for_recovery, application_lifecycle_payload,
    )
    from app.services.execution_recovery import observe_execution_started
    from app.services.execution_recovery_executor import (
        NativeRecoveryRuntimeContext, _acquire_workspace_lease, _recovery_observability,
    )
    from app.services.failed_execution_reconciliation import reconcile_failed_execution_lifecycle
    from app.services.failed_run_cleanup import shield_run_cleanup
    from app.services.retry_preparation import abandon_prepared_retry
    from app.services.retry_node_state import retry_node_state

    async with repair_lock(request):
        source, graph, checkpoint, state, waiting = await load_repair_source(request)
        if run_id == source.run_id or await get_execution(source.workspace, run_id):
            raise ValueError("执行 runId 已使用，请刷新后重试。")
        draft = load_draft(request)
        if not draft:
            raise ValueError("任务修复草稿不存在，请先生成并审阅。")
        validate_draft(request, state, draft)
        context = {**deepcopy(state.get("build_context") or {}), "project_plan": state.get("project_plan") or {}}
        plan = compile_repaired_plan(state, request.taskId, TaskRepairProposal.model_validate(draft["proposal"]), context)
        plan["task_repair_confirmation"] = {
            "source_run_id": source.run_id, "execution_run_id": run_id, "task_id": request.taskId,
            "owner_session_id": request.sessionId, "draft_digest": draft["draftDigest"],
            "base_plan_digest": draft["basePlanDigest"],
        }
        # 交接前保留原源状态；只有确认成功才撤销旧运行控制与自动修复决策。
        state = retry_node_state(state, "build", planning=False)
        state.update(request=checkpoint.request or "执行已确认的单任务修复", resume_from="build",
            retry_source_run_id=run_id, retry_failed_tasks=False, build_retry_task_ids=[],
            build_task_repair_id=request.taskId,
            active_run_id=run_id, active_thread_id=source.thread_id, owner_session_id=source.owner_session_id,
            workflow_scope=source.workflow_scope or "", build_task_plan=plan, tasks=list(plan["task_registry"].values()),
            build_summary={}, repair_task_plan={}, repair_tasks=[], repair_task_plan_path="",
            platform_projection_evidence={}, route_projection_evidence={})
        reconcile_failed_execution_lifecycle(source)
        before = load_application_lifecycle(source.workspace)
        lease, handed_off, committed = None, False, False
        original = None
        committed_digest = build_task_plan_sha256(plan)
        try:
            lifecycle = handoff_workbench_execution_for_recovery(source.workspace,
                source_run_id=source.run_id, new_run_id=run_id, thread_id=source.thread_id,
                phase="build", expected_lifecycle_revision=before.revision, awaiting_build_tasks=waiting)
            handed_off = True
            lease = _acquire_workspace_lease(workspace=source.workspace, source=source, new_run_id=run_id, lifecycle=lifecycle)
            child = await observe_execution_started(workspace=source.workspace, project_id=source.project_id,
                thread_id=source.thread_id, run_id=run_id, workflow_scope=source.workflow_scope,
                first_node="build", owner_session_id=source.owner_session_id)
            observability = _recovery_observability(run_id=run_id, thread_id=source.thread_id,
                project_id=source.project_id, workspace=source.workspace)
            state.update(observability=observability, lifecycle=application_lifecycle_payload(lifecycle))
            with build_task_plan_lifecycle_lock(source.workspace):
                path = build_task_plan_json_path(state)
                original = load_build_task_plan_json(path)
                # CAS 和源码新鲜度在交接后再次检查，防止任何等待窗口发布过期授权。
                validate_draft(request, {**state, "build_task_plan": original}, load_draft(request) or {})
                build_id = f"build-{uuid4().hex}"
                snapshot = write_build_run_task_plan_json(state, build_run_id=build_id, build_task_plan=plan)
                binding = {"build_run_id": build_id, "build_run_plan_path": snapshot, "build_run_plan_sha256": committed_digest}
                state.update(**binding, build_task_plan_path=snapshot)
                write_build_execution_record(state, _build_execution_record(state, binding,
                    _build_runtime_progress(state), progress_source_run_id=run_id, execution_stage="prepared"))
                write_json_atomic(path, plan)
                committed = True
                save_draft(request, {**draft, "status": "confirmed", "executionRunId": run_id})
            runtime = NativeRecoveryRuntimeContext(source_execution=source, child_execution=child,
                recovery_plan=SimpleNamespace(target_node="build"), new_run_id=run_id,
                thread_id=source.thread_id, project_id=source.project_id, workspace=source.workspace,
                workflow_scope=source.workflow_scope, graph=graph,
                fork_config={"configurable": {"thread_id": source.thread_id, "checkpoint_ns": "", "checkpoint_id": str(uuid4())},
                    "metadata": {"run_id": run_id, "thread_id": source.thread_id, "workspace": source.workspace,
                                 "selected_skill_names": list(state.get("selected_skill_names") or [])}},
                observation_config={"configurable": {"thread_id": source.thread_id}},
                fork_snapshot=SimpleNamespace(values=state), lifecycle_payload=state["lifecycle"],
                workspace_lease=lease, observability=observability, heartbeat_task=None,
                restart_node=True, retry_lifecycle_before_handoff=before)
            return runtime, original, committed_digest, draft
        except BaseException:
            try:
                if committed:
                    rollback_repair_plan(request, original, committed_digest, draft)
            finally:
                if handed_off:
                    await shield_run_cleanup(abandon_prepared_retry(workspace=source.workspace, source=source,
                        before=before, new_run_id=run_id, lease=lease))
            raise
