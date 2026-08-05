from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse


DIRECT_MODIFICATION_MODE_MARKER = "<xcodeagent-direct-modification-mode>"

_DIRECT_DISABLED_TOOLS = {"task", "write_todos"}


class DirectModificationMiddleware(AgentMiddleware):
    """在共用 Agent 中仅对快速修改请求移除复杂编排工具。"""

    def __init__(self, required_tools: Sequence[Any] | None = None):
        """保存每次模型调用都必须暴露的工作区工具，防止运行时工具集被中间层丢失。"""

        self.required_tools = tuple(required_tools or ())

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步模型调用前应用快速模式工具策略，主工作流请求保持原样。"""

        prepared = _ensure_required_model_tools(request, self.required_tools)
        return handler(_prepare_direct_model_request(prepared))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步模型调用前应用与同步入口一致的快速模式工具策略。"""

        prepared = _ensure_required_model_tools(request, self.required_tools)
        return await handler(_prepare_direct_model_request(prepared))


def _ensure_required_model_tools(
    request: ModelRequest,
    required_tools: Sequence[Any],
) -> ModelRequest:
    """把缺失的必需工具补回模型请求，已有工具保持原顺序且不重复。"""

    existing_names = {_tool_name(tool) for tool in request.tools}
    missing_tools = [
        tool
        for tool in required_tools
        if _tool_name(tool) and _tool_name(tool) not in existing_names
    ]
    return request if not missing_tools else request.override(
        tools=[*request.tools, *missing_tools]
    )


def _prepare_direct_model_request(request: ModelRequest) -> ModelRequest:
    """识别快速模式并移除复杂编排工具，不覆盖模型运行时配置。"""

    if not _is_direct_modification_messages(request.messages):
        return request
    tools = [tool for tool in request.tools if _tool_name(tool) not in _DIRECT_DISABLED_TOOLS]
    return request.override(tools=tools)


def _is_direct_modification_messages(messages: Any) -> bool:
    """通过快速 Prompt 的稳定标记识别当前 Agent 调用模式。"""

    for message in messages if isinstance(messages, list) else []:
        content = (
            message.get("content", "")
            if isinstance(message, dict)
            else getattr(message, "content", "")
        )
        if isinstance(content, str) and DIRECT_MODIFICATION_MODE_MARKER in content:
            return True
    return False


def _tool_name(tool: Any) -> str:
    """兼容 BaseTool 和 Provider 工具字典，提取稳定工具名称。"""

    if isinstance(tool, dict):
        if tool.get("name"):
            return str(tool["name"])
        function = tool.get("function")
        return str(function.get("name") or "") if isinstance(function, dict) else ""
    return str(getattr(tool, "name", "") or "")
