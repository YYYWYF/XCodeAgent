"""工作台缺少上游规划时，交接到已有规划 Graph 和原确认门。"""

from types import SimpleNamespace
from uuid import uuid4

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus, utc_now
from app.domain.application_revision import ActiveFormalRevision, FormalRevisionBranch, RevisionTarget
from app.services import application_lifecycle as lifecycle_service


_ARTIFACTS = {
    "requirements": ("requirement-spec", ApplicationLifecycleStage.ANALYZING_REQUIREMENT),
    "product_planning": ("product-plan", ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT),
    "ui_confirmation": ("ui-design", ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION),
    "technical_planning_begin": ("technical-plan", ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN),
}


def needs_task_regeneration(state: dict) -> bool:
    """只消费后端明确的来源失效信号，不把普通代码失败误路由为重规划。"""
    return any(item.get("failure_handling") == "replan_build_dag"
               for item in (state.get("repair_task_plan") or {}).get("terminal_failures", []))


def abandon_invalid_pending(workspace: str, owner_session_id: str) -> None:
    """消费同会话的过期草稿，撤销旧确认资格；其他会话草稿保持独立归属。"""
    from app.workspace.task_documents import load_pending_build_task_plan, validate_pending_self_digest, build_task_plan_lifecycle_lock
    from app.services.build_task_plan_lifecycle import abandon_pending_build_task_plan
    state = {"workspace": workspace}
    with build_task_plan_lifecycle_lock(workspace):
        pending = load_pending_build_task_plan(state)
        if pending is None:
            return
        identity = validate_pending_self_digest(pending)
        if not owner_session_id or identity.owner_session_id != owner_session_id:
            raise ValueError("另一会话仍持有待确认任务计划，请先结束该计划。")
        result = abandon_pending_build_task_plan(state, planning_run_id=identity.planning_run_id,
            draft_digest=identity.draft_digest, record_lifecycle=False)
        if result.status != "abandoned":
            raise ValueError("旧任务计划尚未撤销，不能使用它继续开发。")


def _completed_planning_owner(workspace: str, current) -> bool:
    """区分已完成首次规划留下的 Run 镜像与仍有工作、确认门或资源占用的执行。"""
    from app.protocols.application_planning_run_lock import application_planning_run_lock
    from app.services.checkpoint_execution import execution_is_live

    if (current.initialization.stage is not ApplicationLifecycleStage.READY_FOR_WORKBENCH
            or current.initialization.status is not ApplicationLifecycleStatus.COMPLETED
            or current.active_formal_revision is not None or current.pending_revision_impact is not None
            or current.active_executions):
        return False
    locks = current.resource_locks
    if any([locks.application, *locks.pages.values(), *locks.api_contracts.values(),
            *locks.endpoints.values(), *locks.data_sources.values()]):
        return False
    thread_id = current.initialization.thread_id
    if thread_id and application_planning_run_lock(thread_id).locked():
        return False
    # Bootstrap 正常完成会保留上一轮规划 Run；只有真实后台工作已经退出才能交接。
    return not execution_is_live(
        SimpleNamespace(run_id=current.active_run_id, workspace=workspace))


def handoff_artifact_generation(source, *, new_run_id: str, node: str, request: str, target=None):
    """只交接已退出原执行的占用，按明确生成目标投影修订，不增加阶段准入规则。"""
    from app.workspace.task_documents import build_task_plan_lifecycle_lock

    path = lifecycle_service.application_lifecycle_path(source.workspace)
    # 保持 Pending → Lifecycle 的锁顺序，且先核验归属，拒绝交接时不撤销任何草稿。
    with build_task_plan_lifecycle_lock(source.workspace), lifecycle_service._application_lifecycle_lock(path):
        current = lifecycle_service.load_application_lifecycle(source.workspace)
        if current is None:
            raise ValueError("产物重生成缺少当前应用生命周期。")
        if (current.active_run_id not in {None, source.run_id, new_run_id}
                and not _completed_planning_owner(source.workspace, current)):
            raise ValueError("其他执行正在更新正式产物，不能接管其生成。")
        abandon_invalid_pending(source.workspace, str(getattr(source, "owner_session_id", "") or ""))
        execution = current.active_executions.get(source.run_id)
        if target is None and execution and execution.development_target:
            target = execution.development_target.model_dump(mode="json", by_alias=True)
        target = RevisionTarget.model_validate(target) if target else None
        artifact, stage = _ARTIFACTS[node]
        active = current.active_formal_revision
        if active is None:
            active = ActiveFormalRevision(
                changeId=f"chg_{uuid4().hex}", formalBranch=FormalRevisionBranch.DESIGN_STAGE_REVISION,
                sourceThreadId=source.thread_id, sourceRunId=source.run_id, request=request,
                target=target or RevisionTarget(type="application"), impactInteractionId=f"regenerate-{new_run_id}",
                planningThreadId=source.thread_id, status="design_planning", currentArtifact=artifact,
            )
        active = active.model_copy(update={
            "formal_branch": FormalRevisionBranch.DESIGN_STAGE_REVISION,
            "planning_thread_id": source.thread_id, "planning_run_id": new_run_id,
            "status": "design_planning", "current_artifact": artifact,
            "continuation_token_sha256": None, "continuation_consumed_at": None,
            "regeneration_target": target or active.regeneration_target,
        })
        executions = dict(current.active_executions)
        executions.pop(source.run_id, None)
        updated = current.model_copy(update={
            "active_executions": executions,
            "resource_locks": lifecycle_service._resource_locks_without_run(current.resource_locks, source.run_id),
            "active_run_id": new_run_id, "active_formal_revision": active,
            "initialization": current.initialization.model_copy(update={
                "thread_id": source.thread_id, "stage": stage,
                "status": ApplicationLifecycleStatus.RUNNING if node == "technical_planning_begin" else ApplicationLifecycleStatus.FAILED,
            }),
            "updated_at": utc_now(), "revision": current.revision + 1,
        })
        return lifecycle_service.write_application_lifecycle(source.workspace, updated, expected_revision=current.revision)
