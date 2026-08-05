from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.tools.code_graph_context import create_code_graph_context_tool


def create_test_agent(
    model,
    workspace_root: str | None = None,
    *,
    user_skills_backend: BackendProtocol,
    agent_memory_backend: BackendProtocol,
    required_user_skills_prompt: str = "",
):
    """创建只读测试审阅 Deep Agent，并注入用户必选技能。"""

    base_system_prompt = (
        "You are the Test Agent. Review deterministic evidence from install/build, "
        "lint, typecheck, unit tests, API contract checks, and integration tests. "
        "Do not replace command results with guesses. If any check fails, "
        "use the supplied stdout/stderr summaries or virtual workspace log paths. "
        "If those do not expose a cause, report insufficient evidence instead of "
        "guessing. Explain the supported revision request for the Main Agent. Return a concise "
        "validation report. Treat workspace filesystem write tools as unavailable "
        "unless explicitly allowed by the harness."
        " Use code_graph_context to locate affected symbols or related tests before reading source."
    )
    return create_deep_agent(
        name="test-agent",
        model=model,
        system_prompt="\n\n".join(
            part for part in (base_system_prompt, required_user_skills_prompt) if part
        ),
        skills=[USER_SKILLS_VIRTUAL_ROOT],
        memory=[AGENT_MEMORY_VIRTUAL_PATH],
        tools=[create_code_graph_context_tool(workspace_root)],
        backend=create_workspace_backend(
            workspace_root,
            user_skills_backend=user_skills_backend,
            agent_memory_backend=agent_memory_backend,
        ),
        permissions=create_workspace_permissions(
            workspace_root,
            mode="test",
            include_user_skills=True,
            include_agent_memory=True,
        ),
    )
