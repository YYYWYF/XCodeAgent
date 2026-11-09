"""读取已提交的规划现场，不等待覆盖整个生成过程的线程写锁。"""

from typing import Any

from app.domain.execution_recovery import RecoveryExecutionError


async def read_planning_recovery_checkpoint(graph: Any, thread_id: str) -> Any:
    """让存储读取错误沿 AG-UI 明确失败，只有成功读到空现场才代表没有存档。"""
    try:
        return await graph.aget_state({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
    except Exception as exc:
        raise RecoveryExecutionError(
            "APPLICATION_PLANNING_CHECKPOINT_UNAVAILABLE",
            "暂时无法读取规划执行进度，原任务和确认状态已保留，稍后将重新同步。",
        ) from exc


def assert_planning_recovery_owner_unchanged(before: Any, after: Any, *, allow_released_owner: bool = False) -> None:
    """读取期间发生阶段交接或运行接管时拒绝旧快照，下一次查询读取新 owner。"""
    if before is None or after is None:
        if before is after:
            return
    elif (before.initialization.thread_id, before.active_run_id) == (
        after.initialization.thread_id, after.active_run_id
    ):
        return
    elif (allow_released_owner and before.initialization.thread_id == after.initialization.thread_id
          and after.active_run_id is None):
        # 无可执行恢复入口的旧任务允许由现有收口逻辑释放，但不能借此返回旧确认。
        return
    raise RecoveryExecutionError(
        "APPLICATION_PLANNING_SNAPSHOT_CHANGED",
        "规划任务正在交接，稍后将重新同步最新状态。",
    )
