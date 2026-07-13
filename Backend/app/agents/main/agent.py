from deepagents import CompiledSubAgent, create_deep_agent
from deepagents.backends.protocol import BackendProtocol

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.tools.ask_user import ask_user
from app.tools.delete_file import create_delete_file_tool
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


def create_main_agent(
    model,
    frontend,
    data_source,
    test,
    workspace_root: str | None = None,
    *,
    user_skills_backend: BackendProtocol,
):
    backend = create_workspace_backend(
        workspace_root,
        include_builtin_skills=True,
        user_skills_backend=user_skills_backend,
    )
    return create_deep_agent(
        name="main-agent",
        model=model,
        tools=[ask_user, create_delete_file_tool(workspace_root)],
        skills=[BUILTIN_SKILLS_VIRTUAL_ROOT, USER_SKILLS_VIRTUAL_ROOT],
        system_prompt=(
            "You are the application-generation coordinator. Analyze requirements, "
            "create and update RequirementSpec documents, clarify uncertain requirements, "
            "create project-level plans, define API/page/data-source contracts, "
            "coordinate detail confirmation, and delegate implementation and testing when appropriate. "
            "Use ask_user when user input is required before safe planning can continue. "
            f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS} "
            "When deleting a file, use delete_file(file_path=\"/path\") with a virtual absolute path. "
            "Do not read or write sensitive files such as .env, .npmrc, or private keys. "
            "Keep responses concise in this minimal demo."
        ),
        backend=backend,
        permissions=create_workspace_permissions(
            workspace_root,
            mode="main",
            include_builtin_skills=True,
            include_user_skills=True,
        ),
        subagents=[
            CompiledSubAgent(
                name="frontend-generation-agent",
                description="Generates frontend pages from approved plans.",
                runnable=frontend,
            ),
            CompiledSubAgent(
                name="data-source-generation-agent",
                description="Generates data sources, backend APIs, and seed data.",
                runnable=data_source,
            ),
            CompiledSubAgent(
                name="test-agent",
                description="Runs integration and end-to-end checks.",
                runnable=test,
            ),
        ],
    )
