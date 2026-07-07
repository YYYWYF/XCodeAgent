from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.services.page_detail_plan import create_page_detail_plan


def _page_design_prompt(
    project_plan: dict[str, Any],
    confirmed_page_spec: dict[str, Any],
) -> str:
    return (
        "You are the Main Agent for an app-generation workflow.\n"
        "Create a detailed page design from the user-confirmed PageSpec.\n"
        "The ProjectPlan is only context for API contracts, data sources, and dependencies.\n"
        "The PageSpec is the source of truth for page goal, layout, interactions, data sources, and permissions.\n\n"
        f"Confirmed PageSpec:\n{json.dumps(confirmed_page_spec, ensure_ascii=False)}\n\n"
        f"ProjectPlan context:\n{json.dumps(project_plan, ensure_ascii=False)}"
    )


def _invoke_live_main_agent(
    project_plan: dict[str, Any],
    confirmed_page_spec: dict[str, Any],
) -> str:
    # Lazy imports avoid constructing Deep Agents before this live boundary is used.
    from app.agents import create_agent_bundle
    from app.graph.nodes.common import last_agent_text

    result = create_agent_bundle().main.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": _page_design_prompt(project_plan, confirmed_page_spec),
                }
            ]
        }
    )
    return last_agent_text(result)


def design_page_with_main_agent(
    project_plan: dict[str, Any],
    confirmed_page_spec: dict[str, Any],
) -> dict[str, Any]:
    """Use the live Main Agent boundary to create a page detail plan."""

    settings = Settings.from_env()
    agent_note = _invoke_live_main_agent(project_plan, confirmed_page_spec)
    design_source = "main_agent_live"

    detail_plan = create_page_detail_plan(
        project_plan,
        confirmed_page_spec,
        agent_note=agent_note,
    )
    detail_plan["designed_by"] = {
        "agent": "main-agent",
        "mode": "live",
        "model": settings.anthropic_model,
        "source": design_source,
    }
    detail_plan["design_source"] = design_source
    return detail_plan
