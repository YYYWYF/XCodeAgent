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
    """创建只写 Agent Runtime 工程的 Agent/业务代码生成执行器。"""

    base_system_prompt = (
        "You are the Python Application Runtime Coding Agent. Implement only approved owner=agent "
        "or owner=python-business tasks and their formal Contract slices. Inspect the task's platform-compiled "
        "template path policy, prefer reuse or modification of the existing Runtime files, and add "
        "Python only inside the current task's authorized paths. Never create a mechanical "
        "per-Agent wrapper or initialize a second model. Use Python 3.12 and DeepAgents; follow "
        "the selected topology's public edge and business ownership, and write only task allowed_paths under "
        "/agent-runtime/. Never modify frontend, Java backend, formal planning artifacts, API "
        "contracts, or the Build DAG. Do not broaden capabilities, tools, model policy, or "
        "security boundaries beyond the contract. For owner=agent, read and follow "
        f"{BUILTIN_SKILLS_VIRTUAL_ROOT}agent-runtime-generate/SKILL.md and only the references "
        "it routes for the current task. For owner=python-business, follow the approved task packet "
        "and existing template architecture. Return only the structured task result required by "
        "the execution prompt. "
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
            mode="agent_runtime",
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
