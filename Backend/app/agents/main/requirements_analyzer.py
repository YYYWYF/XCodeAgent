from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.services.requirement_spec import create_requirement_spec
from app.tools.clarification import ask_user_about_unclear_requirements


def _requirements_prompt(request: str) -> str:
    return (
        "You are the Main Agent for an app-generation workflow.\n"
        "Analyze the user's application request, identify unclear requirements, "
        "ask clarification questions when needed, and produce a structured RequirementSpec.\n"
        "The RequirementSpec must include app info, user roles, feature modules, pages, "
        "data sources, business flows, acceptance criteria, assumptions, and clarification questions.\n\n"
        f"User request:\n{request}"
    )


def _invoke_live_main_agent(request: str) -> str:
    # Lazy imports avoid constructing Deep Agents before this live boundary is used.
    from app.agents import create_agent_bundle
    from app.graph.nodes.common import last_agent_text

    result = create_agent_bundle().main.invoke(
        {"messages": [{"role": "user", "content": _requirements_prompt(request)}]}
    )
    return last_agent_text(result)


def analyze_requirements_with_main_agent(request: str) -> dict[str, Any]:
    """Use the live Main Agent boundary to create RequirementSpec and clarifications."""

    settings = Settings.from_env()
    agent_note = _invoke_live_main_agent(request)
    analysis_source = "main_agent_live"

    spec = create_requirement_spec(request, agent_note=agent_note)
    clarification = ask_user_about_unclear_requirements(request, spec)
    spec["clarification_questions"] = clarification["questions"]
    spec["assumptions"] = clarification["assumptions"]
    spec["analyzed_by"] = {
        "agent": "main-agent",
        "mode": "live",
        "model": settings.model_name,
        "source": analysis_source,
    }
    spec["analysis_source"] = analysis_source
    clarification["requested_by"] = "main-agent"
    clarification["analysis_source"] = analysis_source
    clarification["analysis_note"] = json.dumps(
        {
            "mode": "live",
            "source": analysis_source,
            "agent_note": agent_note,
        },
        ensure_ascii=False,
    )
    return {
        "requirement_spec": spec,
        "clarification": clarification,
    }
