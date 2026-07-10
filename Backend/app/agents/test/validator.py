from __future__ import annotations

import json
from typing import Any

from app.agents.messages import last_agent_text
from app.config import Settings


def _test_validation_prompt(
    *,
    test_results: list[dict[str, Any]],
    build_results: list[dict[str, Any]],
) -> str:
    return (
        "You are the Test Agent in an app-generation workflow.\n"
        "Review deterministic test evidence and return a concise validation report. "
        "Do not mark failed checks as passed. If any check fails, explain what should "
        "be returned to the Main Agent as a revision request.\n\n"
        f"Deterministic test results:\n{json.dumps(test_results, ensure_ascii=False, indent=2)}\n\n"
        f"Build results:\n{json.dumps(build_results, ensure_ascii=False, indent=2)}"
    )


def _invoke_live_test_agent(
    *,
    test_results: list[dict[str, Any]],
    build_results: list[dict[str, Any]],
    workspace: str | None = None,
) -> str:
    # Lazy import keeps Deep Agent construction at this live execution boundary.
    from app.agents import create_agent_bundle

    result = create_agent_bundle(workspace).test.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": _test_validation_prompt(
                        test_results=test_results,
                        build_results=build_results,
                    ),
                }
            ]
        }
    )
    return last_agent_text(result)


def summarize_tests_with_deep_agent(
    *,
    test_results: list[dict[str, Any]],
    build_results: list[dict[str, Any]],
    workspace: str | None = None,
) -> dict[str, Any]:
    settings = Settings.from_env()
    agent_note = _invoke_live_test_agent(
        test_results=test_results,
        build_results=build_results,
        workspace=workspace,
    )
    return {
        "agent_note": agent_note,
        "reviewed_by": {
            "agent": "test-agent",
            "mode": "live",
            "model": settings.model_name,
            "source": "test_deep_agent",
        },
    }
