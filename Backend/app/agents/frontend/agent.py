from deepagents import create_deep_agent
from deepagents.middleware.permissions import FilesystemPermission


def create_frontend_agent(model):
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
            "and any change request."
        ),
        permissions=[
            FilesystemPermission(
                operations=["read", "write"],
                paths=[
                    "/app/frontend/**",
                    "/app/shared/api/**",
                    "/tests/frontend/**",
                ],
            )
        ],
    )
