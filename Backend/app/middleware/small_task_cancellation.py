"""在同步 Agent 的模型与工具边界观察工作流取消，阻断迟到的线程工作。"""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.agents.small_task.scope import _require_active_small_task


class SmallTaskCancellationMiddleware(AgentMiddleware):
    """让已经取消的局部修复在下一次模型或工具调用前退出。"""

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步模型调用前校验当前修复 Run 仍可执行。"""
        _require_active_small_task()
        return handler(request)

    async def awrap_model_call(
        self, request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步模型调用前执行相同的取消校验。"""
        _require_active_small_task()
        return await handler(request)

    def wrap_tool_call(
        self, request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """同步工具执行前拒绝已取消线程的命令和文件操作。"""
        _require_active_small_task()
        return handler(request)

    async def awrap_tool_call(
        self, request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """异步工具执行前拒绝已取消运行的迟到操作。"""
        _require_active_small_task()
        return await handler(request)
