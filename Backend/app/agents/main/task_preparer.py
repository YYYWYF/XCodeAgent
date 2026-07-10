from __future__ import annotations

import json
from typing import Any

from app.agents.messages import last_agent_text
from app.config import Settings
from app.services.build_task_planner import create_build_task_plan


def _task_preparation_prompt(project_plan: dict[str, Any]) -> str:
    return (
        "You are the Main Agent for an app-generation workflow.\n"
        "Prepare an executable build task DAG from the confirmed ProjectPlan.\n"
        "Use confirmed page_detail_plans for frontend tasks and related data_sources for backend/data tasks.\n"
        "Each task must include owner, dependencies, allowed paths, and acceptance criteria.\n\n"
        f"ProjectPlan:\n{json.dumps(project_plan, ensure_ascii=False)}"
    )


def _invoke_live_main_agent(
    project_plan: dict[str, Any],
    *,
    workspace: str | None = None,
) -> str:
    # Lazy imports avoid constructing Deep Agents before this live boundary is used.
    from app.agents import create_agent_bundle
    result = create_agent_bundle(workspace).main.invoke(
        {
            "messages": [
                {"role": "user", "content": _task_preparation_prompt(project_plan)}
            ]
        }
    )
    return last_agent_text(result)


def prepare_build_tasks_with_main_agent(
    project_plan: dict[str, Any],
    *,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Use the live Main Agent boundary to prepare executable build tasks."""

    settings = Settings.from_env()
    agent_note = _invoke_live_main_agent(project_plan, workspace=workspace)
    preparation_source = "main_agent_live"

    build_task_plan = create_build_task_plan(project_plan, agent_note=agent_note)
    build_task_plan["prepared_by"] = {
        "agent": "main-agent",
        "mode": "live",
        "model": settings.model_name,
        "source": preparation_source,
    }
    build_task_plan["preparation_source"] = preparation_source
    return build_task_plan
