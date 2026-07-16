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


def create_frontend_agent(
    model,
    workspace_root: str | None = None,
    *,
    user_skills_backend: BackendProtocol,
    agent_memory_backend: BackendProtocol,
    required_user_skills_prompt: str = "",
):
    """创建具备前端工作区权限和必选技能指令的 Deep Agent。"""

    backend = create_workspace_backend(
        workspace_root,
        include_builtin_skills=True,
        user_skills_backend=user_skills_backend,
        agent_memory_backend=agent_memory_backend,
    )
    base_system_prompt = (
        "You are the Frontend Generation Agent. Execute only approved frontend "
        "build tasks from the task DAG. Generate or modify frontend code for "
        "layouts, components, interactions, permissions, API integration, loading, "
        "empty, and error states. Add page tests and run frontend lint, typecheck, "
        "and unit tests when available. Do not confirm requirements, do not modify "
        "PageDetail, and do not silently change API contracts. Return a concise "
        "structured implementation report with changed files, commands, status, "
        "and any change request. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS} When deleting a file, "
        "use delete_file(file_path=\"/path\") with a virtual absolute path."
    )
    return create_deep_agent(
        name="frontend-generation-agent",
        model=model,
        system_prompt="\n\n".join(
            part for part in (base_system_prompt, required_user_skills_prompt) if part
        ),
        skills=[BUILTIN_SKILLS_VIRTUAL_ROOT, USER_SKILLS_VIRTUAL_ROOT],
        memory=[AGENT_MEMORY_VIRTUAL_PATH],
        tools=[create_delete_file_tool(workspace_root)],
        backend=backend,
        permissions=create_workspace_permissions(
            workspace_root,
            mode="frontend",
            include_builtin_skills=True,
            include_user_skills=True,
            include_agent_memory=True,
        ),
    )
