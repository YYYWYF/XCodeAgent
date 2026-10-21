"""创建只读工作区问答 Agent。"""

from collections.abc import Awaitable, Callable

from app.agents.execution_control import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


class WorkspaceAssistantReadOnlyMiddleware(AgentMiddleware):
    """只向模型暴露目录查看、文件读取和搜索工具。"""

    _ALLOWED_TOOLS = frozenset({"ls", "read_file", "glob", "grep"})

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步模型请求只保留只读工作区工具。"""

        return handler(request.override(tools=_read_only_tools(request.tools)))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步模型请求只保留只读工作区工具。"""

        return await handler(request.override(tools=_read_only_tools(request.tools)))


def _read_only_tools(tools: list[object]) -> list[object]:
    """按稳定名称筛出 Workspace Assistant 允许使用的读取工具。"""

    return [
        tool
        for tool in tools
        if _tool_name(tool) in WorkspaceAssistantReadOnlyMiddleware._ALLOWED_TOOLS
    ]


def _tool_name(tool: object) -> str:
    """兼容工具对象和模型工具字典，提取工具名称。"""

    if isinstance(tool, dict):
        if tool.get("name"):
            return str(tool["name"])
        function = tool.get("function")
        return str(function.get("name") or "") if isinstance(function, dict) else ""
    return str(getattr(tool, "name", "") or "")


def create_workspace_assistant_agent(
    model,
    workspace_root: str | None = None,
    *,
    user_skills_backend: BackendProtocol,
    agent_memory_backend: BackendProtocol,
    required_user_skills_prompt: str = "",
):
    """创建只允许读取当前工程并以自然语言回答问题的 Deep Agent。"""

    base_system_prompt = (
        "You are DevAgent Studio's read-only Workspace Assistant. Answer questions about the current "
        "workspace by progressively reading only the files needed for the question. Never edit, "
        "write, delete, or execute files and commands. Do not use task, write_todos, or subagents. "
        "Do not inspect dependency, build-output, cache, or version-control directories, including "
        "node_modules, .venv, build, dist, target, .next, .turbo, .git, or /.devagentstudio/cache. "
        "If the question requires excluded files, explain that those paths are outside the assistant's "
        "read scope. "
        "Separate facts observed in files from inferences, and say when the available evidence is "
        "insufficient. Reply in the user's language with concise natural language, not JSON. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}"
    )
    return create_deep_agent(
        name="workspace-assistant-agent",
        model=model,
        system_prompt="\n\n".join(
            part for part in (base_system_prompt, required_user_skills_prompt) if part
        ),
        skills=[USER_SKILLS_VIRTUAL_ROOT],
        memory=[AGENT_MEMORY_VIRTUAL_PATH],
        middleware=[WorkspaceAssistantReadOnlyMiddleware()],
        backend=create_workspace_backend(
            workspace_root,
            user_skills_backend=user_skills_backend,
            agent_memory_backend=agent_memory_backend,
        ),
        permissions=create_workspace_permissions(
            workspace_root,
            mode="workspace_assistant",
            include_user_skills=True,
            include_agent_memory=True,
        ),
    )


def workspace_assistant_prompt(*, request: str, conversation_summary: str) -> str:
    """构造带有界多轮上下文的只读工作区问答输入。"""

    return (
        "Answer the current question using read-only workspace evidence. Do not make any changes.\n\n"
        f"Bounded conversation summary:\n{conversation_summary[-4_000:] or '(empty)'}\n\n"
        f"Current user question:\n{request.strip()}"
    )
