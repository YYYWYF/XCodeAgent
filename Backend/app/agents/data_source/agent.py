from app.agents.data_source.prompts import data_source_system_prompt
from deepagents.backends.protocol import BackendProtocol

from app.agents.data_source.runtime import create_data_source_runtime
from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
    is_data_source_backend_path,
)
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.tools.delete_file import create_delete_file_tool


def create_data_source_agent(
        model,
        workspace_root: str | None = None,
        *,
        user_skills_backend: BackendProtocol,
        required_user_skills_prompt: str = "",
        builtin_skill_names: tuple[str, ...] = (),
):
    """创建具备数据源工作区权限和必选技能指令的 Deep Agent。"""

    runtime_tools = [
        create_delete_file_tool(
            workspace_root,
            path_guard=is_data_source_backend_path,
        ),
    ]
    return create_data_source_runtime(
        name="data-source-generation-agent",
        model=model,
        system_prompt=data_source_system_prompt(
            builtin_skill_names=builtin_skill_names,
            required_user_skills_prompt=required_user_skills_prompt,
        ),
        skills=[BUILTIN_SKILLS_VIRTUAL_ROOT, USER_SKILLS_VIRTUAL_ROOT],
        tools=runtime_tools,
        backend=create_workspace_backend(
            workspace_root,
            include_builtin_skills=True,
            builtin_skill_names=builtin_skill_names,
            user_skills_backend=user_skills_backend,
        ),
        permissions=create_workspace_permissions(
            workspace_root,
            mode="data_source",
            include_builtin_skills=True,
            include_user_skills=True,
        ),
    )
