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
