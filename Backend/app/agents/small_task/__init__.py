"""共享小任务执行 Agent 的声明与结果归一化入口。"""

from app.agents.small_task.agent import create_small_task_agent
from app.agents.small_task.runner import (
    invoke_small_task_agent,
    normalize_small_task_result,
)

__all__ = [
    "create_small_task_agent",
    "invoke_small_task_agent",
    "normalize_small_task_result",
]
