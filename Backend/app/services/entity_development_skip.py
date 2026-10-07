"""按用户明确决定跳过当前工作区的单个实体开发，保留正式实体定义。"""

from pathlib import Path

from app.domain.application_lifecycle import ApplicationLifecycle, utc_now
from app.domain.development_artifacts import EntityDevelopmentProgress
from app.services.application_lifecycle import (
    _application_lifecycle_lock, application_lifecycle_path, write_application_lifecycle,
)
from app.services.development_artifacts import refresh_development_artifacts


def skip_entity_development(workspace: str | Path, *, entity_id: str, reason: str) -> ApplicationLifecycle:
    """在生命周期锁内记录显式跳过，拒绝目录错误、未知实体及仍在执行的绑定。"""

    with _application_lifecycle_lock(application_lifecycle_path(workspace)):
        state = refresh_development_artifacts(workspace)
        if state.development_artifacts.catalog_error:
            raise ValueError(state.development_artifacts.catalog_error)
        progress = state.development_artifacts.entities.get(entity_id)
        if progress is None:
            raise ValueError("当前正式目录中不存在指定实体。")
        if progress.initial_development_status in {"completed", "skipped"}:
            return state
        if any(
            execution.scope == "data_source" and execution.target_id == entity_id
            and execution.status in {"running", "stopping", "awaiting_user"}
            for execution in state.active_executions.values()
        ):
            raise ValueError("请先结束当前实体绑定执行，再跳过实体开发。")
        state.development_artifacts.entities[entity_id] = EntityDevelopmentProgress(
            initialDevelopmentStatus="skipped", skippedAt=utc_now(), skipReason=reason,
        )
        updated = state.model_copy(update={"revision": state.revision + 1, "updated_at": utc_now()})
        return write_application_lifecycle(workspace, updated, expected_revision=state.revision)
