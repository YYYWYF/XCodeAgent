from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.services.project_plan import create_project_plan


def _planning_prompt(requirement_spec: dict[str, Any]) -> str:
    return (
        "You are the Main Agent for an app-generation workflow.\n"
        "Create a project-level plan from the RequirementSpec. The plan must cover:\n"
        "- architecture\n"
        "- API contracts\n"
        "- frontend pages\n"
        "- data sources\n"
        "- task inputs for frontend and data-source generation agents\n"
        "- coordination strategy for detail confirmation, build, and testing\n\n"
        "Return concise JSON if possible. RequirementSpec:\n"
        f"{json.dumps(requirement_spec, ensure_ascii=False)}"
    )


def _invoke_live_main_agent(requirement_spec: dict[str, Any]) -> str:
    # Import lazily to avoid constructing Deep Agents before this live boundary is used.
    from app.agents import create_agent_bundle
    from app.graph.nodes.common import last_agent_text

    result = create_agent_bundle().main.invoke(
        {"messages": [{"role": "user", "content": _planning_prompt(requirement_spec)}]}
    )
    return last_agent_text(result)


def plan_project_with_main_agent(requirement_spec: dict[str, Any]) -> dict[str, Any]:
    """Use the live Main Agent planning boundary to produce a ProjectPlan."""

    settings = Settings.from_env()
    agent_note = _invoke_live_main_agent(requirement_spec)
    planning_source = "main_agent_live"

    plan = create_project_plan(
        requirement_spec,
        agent_note=agent_note,
        planning_source=planning_source,
    )
    plan["planned_by"] = {
        "agent": "main-agent",
        "mode": "live",
        "model": settings.model_name,
        "source": planning_source,
    }
    return plan
