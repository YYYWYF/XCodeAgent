"""创建只负责单元测试文件的 Deep Agent。"""

from collections.abc import Awaitable, Callable
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from app.agents.test_generation.scope import ScopedTestGenerationBackend
from app.agents.workspace_scope import create_workspace_backend, create_workspace_permissions
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


class TestGenerationMiddleware(AgentMiddleware):
    """从默认 Deep Agent 工具集中硬性移除命令和子 Agent 能力。"""

    _DISABLED_TOOLS = frozenset({"execute", "task", "write_todos"})

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步模型调用前过滤越权工具。"""

        return handler(request.override(tools=_without_disabled_tools(request.tools)))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步模型调用前过滤越权工具。"""

        return await handler(request.override(tools=_without_disabled_tools(request.tools)))


def _without_disabled_tools(tools: list[Any]) -> list[Any]:
    """按稳定工具名移除命令、todo 和子 Agent 工具。"""

    return [
        tool
        for tool in tools
        if str(
            (tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", ""))
            or ""
        )
        not in TestGenerationMiddleware._DISABLED_TOOLS
    ]


def create_test_generation_agent(
    model,
    workspace_root: str | None = None,
    *,
    user_skills_backend: BackendProtocol,
    agent_memory_backend: BackendProtocol,
    required_user_skills_prompt: str = "",
):
    """创建只允许读源码和写测试文件的 TestGeneration Agent。"""

    system_prompt = (
        "You are the TestGeneration Agent. Generate or update only the smallest set of "
        "unit tests for the current business-code change. Read the supplied diff first, "
        "then the changed source, related tests, confirmed build/task artifacts and only "
        "the direct dependencies needed for the test. Do not modify production code, "
        "package manifests, build configuration, formal .xcodeagent artifacts, CSS, "
        "snapshots, or end-to-end tests. Frontend tests must be flat under frontend/tests "
        "and use <module>-<feature>.test.ts(x). Backend tests must mirror the Java package "
        "under backend/src/test/java and use <Class>Test.java. Use Jest/RTL/factory mocks "
        "already present in the project and JUnit 5/Mockito for Java services. Cover only "
        "the main path and one important branch. Backend tests must remain Java 8 compatible "
        "(no var, record, List.of, text blocks or modern APIs). Prefer pure Service tests; "
        "never create tests for mapping-only classes such as *Assembler, *Converter or "
        "*Mapper, including MapStruct generated implementations, DTOs, entities, "
        "configuration or simple getters/setters. If all changed files are excluded, "
        "or there is no suitable target, return a skipped JSON result. Use WebMvcTest "
        "only for changed route or validation contracts. Do not run commands. Return one "
        "JSON object with status, "
        "summary, affected_layers, test_files, mappings, warnings and behaviors. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}"
    )
    backend = ScopedTestGenerationBackend(
        create_workspace_backend(
            workspace_root,
            user_skills_backend=user_skills_backend,
            agent_memory_backend=agent_memory_backend,
        )
    )
    return create_deep_agent(
        name="test-generation-agent",
        model=model,
        system_prompt="\n\n".join(
            part for part in (system_prompt, required_user_skills_prompt) if part
        ),
        middleware=[TestGenerationMiddleware()],
        skills=[USER_SKILLS_VIRTUAL_ROOT],
        memory=[AGENT_MEMORY_VIRTUAL_PATH],
        backend=backend,
        permissions=create_workspace_permissions(
            workspace_root,
            mode="test_generation",
            include_user_skills=True,
            include_agent_memory=True,
        ),
    )
