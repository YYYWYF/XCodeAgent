"""以当前线程最新现场核对规划恢复归属，不由旧生命周期 Run 排除当前失败。"""

from pathlib import Path
from typing import Any

from app.domain.application_lifecycle import ApplicationLifecycle
from app.domain.execution_recovery import DurableExecutionRecord, RecoveryExecutionError


async def planning_source_is_current(
    source: DurableExecutionRecord, graph: Any, lifecycle: ApplicationLifecycle | None,
) -> bool:
    """只允许同线程最新 Run 接替已经退出、仍留在生命周期中的旧规划 owner。"""

    if source.execution_kind != "application_planning" or lifecycle is None:
        return True
    owner_run_id = lifecycle.active_run_id
    if owner_run_id is None:
        return True
    get_state = getattr(graph, "aget_state", None)
    if get_state is None:
        return False
    from app.services.retry_preparation import await_checkpoint_preparation

    try:
        # 只核对当前 root；历史中找到同 Run 失败不足以取得当前应用的运行归属。
        # 沿用平台恢复准备期限及取消登记，超时仍保留原恢复入口。
        snapshot = await await_checkpoint_preparation(
            get_state({"configurable": {"thread_id": source.thread_id, "checkpoint_ns": ""}}),
            source.run_id,
        )
    except Exception as exc:
        raise RecoveryExecutionError(
            "RETRY_CHECKPOINT_UNAVAILABLE", "暂时无法读取执行进度，请稍后重试。原恢复入口已保留。",
        ) from exc
    values = getattr(snapshot, "values", {}) or {}
    config = (getattr(snapshot, "config", {}) or {}).get("configurable", {})
    if (not isinstance(values, dict) or values.get("active_run_id") != source.run_id
            or config.get("checkpoint_ns")):
        return False
    if owner_run_id == source.run_id:
        return True
    if lifecycle.initialization.thread_id != source.thread_id:
        return False
    from app.persistence.execution_recovery import get_execution
    from app.services.checkpoint_execution import execution_is_live

    owner = await get_execution(source.workspace, owner_run_id)
    if (owner is None or owner.execution_kind != "application_planning"
            or owner.thread_id != source.thread_id
            or Path(owner.workspace).resolve() != Path(source.workspace).resolve()
            or execution_is_live(owner)):
        return False
    workspace = values.get("workspace")
    return bool(
        values.get("workflow_scope") == "application_planning"
        and isinstance(workspace, str) and workspace
        and Path(workspace).resolve() == Path(source.workspace).resolve()
    )
