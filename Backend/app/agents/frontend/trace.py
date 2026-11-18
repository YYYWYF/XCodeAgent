"""前端 Build Agent 的本地追踪入口。"""

from contextlib import AbstractContextManager
from typing import Any

from app.observability.agent_execution_trace import AgentExecutionTrace, agent_execution_trace


def frontend_agent_trace(
    *, workspace: str | None, tasks: list[dict[str, Any]],
) -> AbstractContextManager[AgentExecutionTrace | None]:
    """按独立开关为本次前端调用创建追踪，使用前端专属目录。"""
    return agent_execution_trace(
        workspace=workspace,
        tasks=tasks,
        enabled_env="FRONTEND_AGENT_TRACE_ENABLED",
        directory_name="frontend-agent",
        agent_kind="frontend",
    )
