"""代码审查 Agent 的严格工具白名单策略。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse


_CODE_ANALYZE_ALLOWED_TOOLS = {
    "glob",
    "grep",
    "list_mayun_frontend_code_review_skill",
    "load_mayun_frontend_code_review_skill",
    "ls",
    "read_file",
    "save_code_audit_report",
}


class CodeAnalyzeToolPolicyMiddleware(AgentMiddleware):
    """只向专用审查 Agent 暴露读取、检索和受控报告工具。"""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步调用模型前应用代码审查工具白名单。"""

        return handler(_restricted_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步调用模型前应用与同步入口一致的工具白名单。"""

        return await handler(_restricted_request(request))


def _restricted_request(request: ModelRequest) -> ModelRequest:
    """删除代码审查不需要的写文件、命令、计划和子代理工具。"""

    tools = [
        candidate
        for candidate in request.tools
        if str(getattr(candidate, "name", "") or "") in _CODE_ANALYZE_ALLOWED_TOOLS
    ]
    return request.override(tools=tools)
