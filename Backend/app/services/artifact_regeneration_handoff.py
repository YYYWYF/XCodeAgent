"""工作台缺少上游规划时，交接到已有规划 Graph 和原确认门。"""

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


def handoff_artifact_generation(source, *, new_run_id: str, node: str, request: str):
    """只交接已退出原执行的占用，按明确生成目标投影修订，不增加阶段准入规则。"""
    path = lifecycle_service.application_lifecycle_path(source.workspace)
    with lifecycle_service._application_lifecycle_lock(path):
        current = lifecycle_service.load_application_lifecycle(source.workspace)
        artifact, stage = _ARTIFACTS[node]
        active = current.active_formal_revision
        if active is None:
            active = ActiveFormalRevision(
                changeId=f"chg_{uuid4().hex}", formalBranch=FormalRevisionBranch.DESIGN_STAGE_REVISION,
                sourceThreadId=source.thread_id, sourceRunId=source.run_id, request=request,
                target=RevisionTarget(type="application"), impactInteractionId=f"regenerate-{new_run_id}",
                planningThreadId=source.thread_id, status="design_planning", currentArtifact=artifact,
            )
        active = active.model_copy(update={
            "formal_branch": FormalRevisionBranch.DESIGN_STAGE_REVISION,
            "planning_thread_id": source.thread_id, "planning_run_id": new_run_id,
            "status": "design_planning", "current_artifact": artifact,
            "continuation_token_sha256": None, "continuation_consumed_at": None,
        })
        executions = dict(current.active_executions)
        executions.pop(source.run_id, None)
        updated = current.model_copy(update={
            "active_executions": executions,
            "resource_locks": lifecycle_service._resource_locks_without_run(current.resource_locks, source.run_id),
            "active_run_id": new_run_id, "active_formal_revision": active,
            "initialization": current.initialization.model_copy(update={
                "thread_id": source.thread_id, "stage": stage, "status": ApplicationLifecycleStatus.FAILED,
            }),
            "updated_at": utc_now(), "revision": current.revision + 1,
        })
        return lifecycle_service.write_application_lifecycle(source.workspace, updated, expected_revision=current.revision)
