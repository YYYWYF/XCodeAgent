from deepagents import create_deep_agent

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)


def create_test_agent(model, workspace_root: str | None = None):
    return create_deep_agent(
        name="test-agent",
        model=model,
        system_prompt=(
            "You are the Test Agent. Review deterministic evidence from install/build, "
            "lint, typecheck, unit tests, API contract checks, integration tests, and "
            "E2E tests. Do not replace command results with guesses. If any check fails, "
            "explain the likely revision request for the Main Agent. Return a concise "
            "validation report. Treat workspace filesystem write tools as unavailable "
            "unless explicitly allowed by the harness."
        ),
        backend=create_workspace_backend(workspace_root),
        permissions=create_workspace_permissions(workspace_root, mode="test"),
    )
