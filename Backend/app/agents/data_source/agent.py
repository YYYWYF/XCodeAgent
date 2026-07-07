from deepagents import create_deep_agent
from deepagents.middleware.permissions import FilesystemPermission


def create_data_source_agent(model):
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
            "status, and any change request."
        ),
        permissions=[
            FilesystemPermission(
                operations=["read", "write"],
                paths=[
                    "/app/backend/**",
                    "/app/shared/api/**",
                    "/tests/backend/**",
                ],
            )
        ],
    )
