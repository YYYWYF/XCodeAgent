from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.tools.delete_file import create_delete_file_tool
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


def create_data_source_agent(
    model,
    workspace_root: str | None = None,
    *,
    user_skills_backend: BackendProtocol,
):
    return create_deep_agent(
        name="data-source-generation-agent",
        model=model,
        system_prompt=(
            "You are the Data Source Generation Agent. Execute only approved "
            "data-source build tasks from the task DAG. Generate or modify data "
            "models, migrations, seed or mock data, APIs, validation, permissions, "
            "and backend tests while obeying the confirmed API contract. If the "
            "contract cannot be implemented, return a change request; never silently "
            "change the contract. Do not confirm requirements and do not modify "
            "RequirementSpec, PageSpec, ProjectPlan, or the task DAG directly. Return "
            "a concise structured implementation report with changed files, commands, "
            "status, and any change request. "
            f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS} When deleting "
            "a file, use delete_file(file_path=\"/path\") with a virtual absolute path."
        ),
        skills=[USER_SKILLS_VIRTUAL_ROOT],
        tools=[create_delete_file_tool(workspace_root)],
        backend=create_workspace_backend(
            workspace_root,
            user_skills_backend=user_skills_backend,
        ),
        permissions=create_workspace_permissions(
            workspace_root,
            mode="data_source",
            include_user_skills=True,
        ),
    )
