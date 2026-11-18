"""Data Source Agent 的本地追踪入口。"""

from contextlib import AbstractContextManager
from typing import Any

from app.observability.agent_execution_trace import AgentExecutionTrace as DataSourceTrace
from app.observability.agent_execution_trace import agent_execution_trace


def data_source_trace(
    *, workspace: str | None, tasks: list[dict[str, Any]],
) -> AbstractContextManager[DataSourceTrace | None]:
    """沿用 Data Source 专属开关、目录和事件字段。"""
    return agent_execution_trace(
        workspace=workspace,
        tasks=tasks,
        enabled_env="DATASOURCE_AGENT_TRACE_ENABLED",
        directory_name="datasource-agent",
    )
