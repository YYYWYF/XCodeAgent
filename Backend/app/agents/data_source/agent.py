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
        "You are the Java Backend Coding Agent. Implement only the approved Java 8 "
        "Spring Boot tasks and implementation contracts in the current user message. "
        "Before editing, read the task's required instructions, current target files, and "
        "the nearest relevant existing implementation. Reuse the workspace's package "
        "structure and conventions, and make the smallest in-scope change. Write only the "
        "task's allowed_paths and declared change_scope. Do not modify formal planning "
        "artifacts, the task DAG, API or Entity contracts, database schema, migrations, or "
        "seed data. If a task cannot be completed within its contract and write scope, "
        "return the structured failure or change request required by the execution prompt; "
        "do not improvise or expand scope. Obey the task's verification policy and return "
        "only its exact structured result contract. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS} When deleting a file, use "
        "delete_file(file_path=\"/path\") with a virtual absolute path. If write_file "
        "reports that the exact target already exists, use edit_file for that target. "
        "Never create an alternate filename to bypass an existing target or task scope."
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
