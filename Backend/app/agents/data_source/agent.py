from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.middleware.direct_modification import DirectModificationMiddleware
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.tools.delete_file import create_delete_file_tool
from app.tools.execute import create_execute_tool
from app.tools.mysql_info import create_get_mysql_config_tool
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


def create_data_source_agent(
        model,
        workspace_root: str | None = None,
        *,
        user_skills_backend: BackendProtocol,
        agent_memory_backend: BackendProtocol,
        required_user_skills_prompt: str = "",
):
    """创建具备数据源工作区权限和必选技能指令的 Deep Agent。"""

    base_system_prompt = (
        "You are the Data Source Coding Agent. Follow the task-level Skill routing and "
        "execution contract in the current user message. Generate or modify Java 8 Spring "
        "Boot backend implementation code for confirmed database or external API entities. "
        "Database schema execution, migrations, seed data, formal planning artifacts, and "
        "repository verification belong to other workflow phases. If the confirmed API "
        "contract or EntityDesign cannot be implemented, return a change request; never "
        "silently change either contract. Return "
        "a concise structured implementation report with changed files, verification notes, "
        "status, and any change request. Follow the current execution prompt for whether "
        "project-level verification is allowed; do not invent validation commands. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS} When deleting "
        "a file, use delete_file(file_path=\"/path\") with a virtual absolute path.\n"
        "IMPORTANT: If write_file returns an error that a file already exists, "
        "use edit_file to modify the existing file instead of retrying write_file "
        "with the same path. If you must create a new file, use a unique filename "
        "(e.g. append a number or timestamp). Never retry write_file with the same "
        "path more than once."
    )
    execute_tool = create_execute_tool(workspace_root)
    runtime_tools = [
        create_delete_file_tool(workspace_root),
        execute_tool,
        create_get_mysql_config_tool(workspace_root),
    ]
    return create_deep_agent(
        name="data-source-generation-agent",
        model=model,
        system_prompt="\n\n".join(
            part for part in (base_system_prompt, required_user_skills_prompt) if part
        ),
        skills=[BUILTIN_SKILLS_VIRTUAL_ROOT, USER_SKILLS_VIRTUAL_ROOT],
        memory=[AGENT_MEMORY_VIRTUAL_PATH],
        tools=runtime_tools,
        middleware=[DirectModificationMiddleware(required_tools=[execute_tool])],
        backend=create_workspace_backend(
            workspace_root,
            include_builtin_skills=True,
            user_skills_backend=user_skills_backend,
            agent_memory_backend=agent_memory_backend,
        ),
        permissions=create_workspace_permissions(
            workspace_root,
            mode="data_source",
            include_builtin_skills=True,
            include_user_skills=True,
            include_agent_memory=True,
        ),
    )
