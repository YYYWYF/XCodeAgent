"""在开发入口 checkpoint 就绪后，原子提交正式修订的执行交接。"""

import secrets
from pathlib import Path

from app.domain.application_lifecycle import (
    ApplicationInitialization, ApplicationLifecycleStage, ApplicationLifecycleStatus, utc_now,
)
from app.services.artifact_invalidation import canonical_sha256


def validated_continuation(current, *, change_id: str, token: str, technical_plan_path: str | Path):
    """复用既有续接身份、版本和产物检查，只读验证，不消费凭据。"""
    from app.services.application_lifecycle import ApplicationLifecycleConflictError
    from app.services.application_revision_lifecycle import _token_sha256

    active = current.active_formal_revision
    if active is None or active.change_id != change_id:
        raise ApplicationLifecycleConflictError("continuation changeId 不匹配。")
    if active.status != "continuation_ready" or active.continuation_consumed_at is not None:
        raise ApplicationLifecycleConflictError("continuation 已消费或当前不可用。")
    if active.continuation_lifecycle_revision != current.revision:
        raise ApplicationLifecycleConflictError("continuation 绑定的 lifecycle revision 已变化。")
    if not secrets.compare_digest(str(active.continuation_token_sha256 or ""), _token_sha256(token)):
        raise ApplicationLifecycleConflictError("continuation token 无效。")
    if active.technical_plan_sha256 != canonical_sha256(technical_plan_path):
        raise ApplicationLifecycleConflictError("TechnicalPlan 已变化，continuation 作废。")
    return active


def consumed_continuation_state(current, **identity):
    """仅在内存中消费续接，调用方与开发执行及资源登记一起原子落盘。"""
    active = validated_continuation(current, **identity)
    return current.model_copy(update={
        "active_formal_revision": active.model_copy(update={
            "status": "building", "continuation_token_sha256": None, "continuation_consumed_at": utc_now(),
        }),
        "initialization": ApplicationInitialization(
            stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            status=ApplicationLifecycleStatus.COMPLETED, threadId=active.planning_thread_id,
        ),
    })


async def persist_revision_build_entry(graph, *, inputs: dict, state: dict, thread_id: str, run_id: str):
    """消费前提交真实开发入口和原提问；不执行 Agent，不改变 Graph 的异步持久化策略。"""
    from app.services.retry_preparation import await_checkpoint_preparation

    entry = {**state, "workspace": inputs["workspace"], "project_id": inputs.get("project_id"),
             "workflow_scope": inputs.get("workflow_scope") or "",
             "resume_from": inputs["resume_from"], "phase": inputs["resume_from"],
             "status": "in_progress"}
    # workflow_entry 无业务副作用；沿原有路由提交 next=实际开发入口，统一重试可直接定位。
    return await await_checkpoint_preparation(graph.aupdate_state(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        entry, as_node="workflow_entry",
    ), run_id)
