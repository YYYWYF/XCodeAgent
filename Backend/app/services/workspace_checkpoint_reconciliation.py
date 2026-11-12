"""Workspace Attach 校准无执行现场的占用，保留文档资产及其原有确认事实。"""

import json
from uuid import uuid4
from pathlib import Path
from types import SimpleNamespace

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.persistence.checkpoints import workflow_checkpoint_db_path
from app.services import application_lifecycle as lifecycle_service
from app.services.checkpoint_execution import execution_is_live
from app.services.recovery_checkpoint import checkpoint_thread_exists


def _run_is_live(workspace: str, run_id: str) -> bool:
    """同时保护 Graph、后台线程、子进程及首个 checkpoint 前的已接受任务。"""
    return execution_is_live(SimpleNamespace(run_id=run_id, workspace=workspace))


def _confirmed_planning_assets(workspace: str) -> bool:
    """只使用正式文档已有的确认事实放行工作台，不替文件确认或恢复旧任务。"""
    root = Path(workspace) / WORKSPACE_ARTIFACT_DIR
    for relative in ("specs/requirement-spec.json", "plans/product-plan.json", "plans/technical-plan.json"):
        try:
            value = json.loads((root / relative).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        if not isinstance(value, dict) or value.get("confirmation_status") != "confirmed":
            return False
    return True


async def reconcile_missing_workspace_checkpoints(workspace: str) -> None:
    """只在显式 Attach 释放无现场且已退出的任务；读取异常保留原占用。"""
    from app.protocols.application_planning_run_lock import application_planning_run_lock
    from app.protocols.workflow.run_control import workflow_run_registry
    from app.services.ui_design_generation_pool import active_ui_design_count
    from app.workspace.run_lease import workspace_run_leases

    before = lifecycle_service.load_application_lifecycle(workspace)
    if before is None:
        return
    missing = set()
    for run_id, execution in before.active_executions.items():
        if not _run_is_live(workspace, run_id) and not await checkpoint_thread_exists(workspace, execution.thread_id):
            missing.add(run_id)
    if not workflow_checkpoint_db_path(workspace=workspace).is_file():
        # 完全没有执行库时也处理只有锁、没有 execution 的残留，真实后台进程仍受保护。
        locks = before.resource_locks
        claims = [locks.application, *locks.pages.values(), *locks.api_contracts.values(),
                  *locks.endpoints.values(), *locks.data_sources.values()]
        missing.update(lock.run_id for lock in claims if lock and not _run_is_live(workspace, lock.run_id))
    thread_id = before.initialization.thread_id or ""
    planning_missing = not await checkpoint_thread_exists(workspace, thread_id)
    # 接受事务尚未发布 Run 时也不能释放占用；这是实际执行保护，不以文件里的 running 猜测。
    planning_idle = (not application_planning_run_lock(thread_id).locked()
                     and not workflow_run_registry.active_run_ids(workspace)
                     and not active_ui_design_count(workspace)
                     and not (before.active_run_id and _run_is_live(workspace, before.active_run_id)))
    ready = (planning_missing and planning_idle and before.active_formal_revision is None
             and _confirmed_planning_assets(workspace))
    pending_review = None
    if planning_missing and planning_idle:
        from app.services.document_review_assets import find_document_review
        try:
            pending_review = find_document_review(workspace, planning=True)
        except ValueError:
            # 内容校验问题由 scoped Get 明确展示；不把旧正式版本当作新草稿已确认。
            ready = False
        if pending_review:
            ready = False
    with lifecycle_service._application_lifecycle_lock(lifecycle_service.application_lifecycle_path(workspace)):
        current = lifecycle_service.load_application_lifecycle(workspace)
        if current is None or current.revision != before.revision:
            return
        # 所有异步读取完成后重新核对实际工作，不能清掉读取期间刚开始的新执行。
        removed = {run_id for run_id in missing if not _run_is_live(workspace, run_id)}
        executions = {key: value for key, value in current.active_executions.items() if key not in removed}
        locks = current.resource_locks
        for run_id in removed:
            locks = lifecycle_service._resource_locks_without_run(locks, run_id)
        updates = {"active_executions": executions, "resource_locks": locks}
        if current.active_run_id in removed or (
            planning_missing and planning_idle and current.active_run_id not in executions
        ):
            # 保留仍有现场的工作台执行引用；只清理已失去现场的镜像归属。
            latest = max(executions.values(), key=lambda item: item.updated_at) if executions else None
            updates["active_run_id"] = latest.run_id if latest else None
        if planning_missing and planning_idle:
            if pending_review:
                updates.update(initialization=current.initialization.model_copy(update={
                    "stage": ApplicationLifecycleStage(pending_review.stage),
                    "status": ApplicationLifecycleStatus.AWAITING_USER,
                    "thread_id": current.initialization.thread_id or "document-review-" + uuid4().hex,
                }), error=None)
            elif ready:
                updates.update(initialization=current.initialization.model_copy(update={
                    "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    "status": ApplicationLifecycleStatus.COMPLETED,
                }), error=None)
        if all(getattr(current, key) == value for key, value in updates.items()):
            return
        lifecycle_service.write_application_lifecycle(workspace, current.model_copy(update={
            **updates, "updated_at": lifecycle_service.utc_now(), "revision": current.revision + 1,
        }), expected_revision=current.revision)
        for run_id in removed:
            workspace_run_leases.release_run(workspace_root=workspace, run_id=run_id)
