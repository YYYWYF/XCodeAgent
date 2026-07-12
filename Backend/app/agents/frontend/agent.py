from deepagents import create_deep_agent

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT
from app.tools.delete_file import create_delete_file_tool
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


def create_frontend_agent(model, workspace_root: str | None = None):
    backend = create_workspace_backend(
        workspace_root,
        include_builtin_skills=True,
    )
    return create_deep_agent(
        name="frontend-generation-agent",
        model=model,
        system_prompt=(
            "You are the Frontend Generation Agent. Execute only approved frontend "
            "build tasks from the task DAG. Generate or modify frontend code for "
            "layouts, components, interactions, permissions, API integration, loading, "
            "empty, and error states. Add page tests and run frontend lint, typecheck, "
            "and unit tests when available. Do not confirm requirements, do not modify "
            "PageSpec, and do not silently change API contracts. Return a concise "
            "structured implementation report with changed files, commands, status, "
            "and any change request. "
            f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS} When deleting a file, "
            "use delete_file(file_path=\"/path\") with a virtual absolute path."
        ),
        skills=[BUILTIN_SKILLS_VIRTUAL_ROOT],
        tools=[create_delete_file_tool(workspace_root)],
        backend=backend,
        permissions=create_workspace_permissions(
            workspace_root,
            mode="frontend",
            include_builtin_skills=True,
        ),
    )
