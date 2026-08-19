from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.tools.delete_file import create_delete_file_tool
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


def create_agent_runtime_agent(
    model,
    workspace_root: str | None = None,
    *,
    user_skills_backend: BackendProtocol,
    agent_memory_backend: BackendProtocol,
    required_user_skills_prompt: str = "",
):
    """创建只能实现生成应用 Python Agent Runtime 的 Deep Agent。"""

    base_system_prompt = (
        "You are the Python Agent Runtime Coding Agent. Implement only approved owner=agent "
        "tasks and their TechnicalPlan agent_contracts. Use Python 3.12 and DeepAgents, keep "
        "the sidecar behind the Java gateway, and implement the declared internal AG-UI SSE "
        "runtime behavior and tool adapters. Write only task allowed_paths under "
        "/agent-runtime/. Never modify frontend, Java backend, formal planning artifacts, API "
        "contracts, or the Build DAG. Do not broaden capabilities, tools, model policy, or "
        "security boundaries beyond the contract. Return only the structured task result "
        "required by the execution prompt. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}"
    )
    return create_deep_agent(
        name="agent-runtime-generation-agent",
        model=model,
        system_prompt="\n\n".join(
            part for part in (base_system_prompt, required_user_skills_prompt) if part
        ),
        skills=[BUILTIN_SKILLS_VIRTUAL_ROOT, USER_SKILLS_VIRTUAL_ROOT],
        memory=[AGENT_MEMORY_VIRTUAL_PATH],
        tools=[
            create_delete_file_tool(workspace_root),
        ],
        backend=create_workspace_backend(
            workspace_root,
            include_builtin_skills=True,
            user_skills_backend=user_skills_backend,
            agent_memory_backend=agent_memory_backend,
        ),
        permissions=create_workspace_permissions(
            workspace_root,
            mode="agent_runtime",
            include_builtin_skills=True,
            include_user_skills=True,
            include_agent_memory=True,
        ),
    )
