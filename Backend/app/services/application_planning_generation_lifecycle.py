"""Application Planning 模型生成阶段的统一 lifecycle 重入辅助。"""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.application_lifecycle import (
    ApplicationLifecycle,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.services.application_lifecycle import (
    load_application_lifecycle,
    persist_application_lifecycle_transition,
)


def ensure_generation_running(
    workspace: str,
    *,
    stage: ApplicationLifecycleStage,
    active_run_id: str | None,
    predecessor_stages: Iterable[ApplicationLifecycleStage] = (),
) -> ApplicationLifecycle:
    """将合法 predecessor、失败重入和正常运行统一收敛为 RUNNING。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        raise ValueError("Application Planning generation 缺少 lifecycle。")
    current_stage = current.initialization.stage
    current_status = current.initialization.status
    allowed_predecessors = set(predecessor_stages)
    if current_stage == stage and current_status is ApplicationLifecycleStatus.RUNNING:
        return current
    if current_stage == stage and current_status in {
        ApplicationLifecycleStatus.FAILED,
        ApplicationLifecycleStatus.CANCELLED,
    }:
        return persist_application_lifecycle_transition(
            workspace,
            stage=stage,
            status=ApplicationLifecycleStatus.RUNNING,
            active_run_id=active_run_id,
        )
    if current_stage in allowed_predecessors:
        return persist_application_lifecycle_transition(
            workspace,
            stage=stage,
            status=ApplicationLifecycleStatus.RUNNING,
            active_run_id=active_run_id,
        )
    raise ValueError(
        "Application Planning generation lifecycle 不在允许的重入窗口："
        f"current={current_stage.value}/{current_status.value}, expected={stage.value}"
    )


__all__ = ["ensure_generation_running"]
