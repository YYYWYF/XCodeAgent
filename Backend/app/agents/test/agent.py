from deepagents import create_deep_agent


def create_test_agent(model):
    return create_deep_agent(
        name="test-agent",
        model=model,
        system_prompt=(
            "You are the Test Agent. Review deterministic evidence from install/build, "
            "lint, typecheck, unit tests, API contract checks, integration tests, and "
            "E2E tests. Do not replace command results with guesses. If any check fails, "
            "explain the likely revision request for the Main Agent. Return a concise "
            "validation report."
        ),
    )
