from __future__ import annotations

import json
from typing import Any

from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.project_plan import create_project_plan


def _planning_prompt(requirement_spec: dict[str, Any]) -> str:
    return (
        "You are the project-planning model for an app-generation workflow.\n"
        "This is a planning-only boundary. Do not call tools, do not call subagents, "
        "do not delegate tasks, and do not generate or modify code.\n"
        "Create a project-level planning document from the RequirementSpec.\n"
        "Return only one JSON object, without markdown fences or commentary.\n"
        "The JSON object must include these top-level keys:\n"
        "- requirements_overview: app goal, roles, modules, flows, acceptance focus\n"
        "- project_acceptance_criteria: whole-requirement acceptance criteria for project completion\n"
        "- architecture: frontend, backend, data, testing\n"
        "- api_contracts: API resource contracts with endpoints\n"
        "- frontend_pages: page list with path, module_id, data_dependencies, states, permissions\n"
        "- data_sources: data source list with entities, schema, seed strategy\n"
        "- page_data_dependencies: explicit dependencies between pages, data sources, and API contracts\n"
        "- permission_model: roles, page access, operation permissions\n"
        "- task_inputs: frontend and data_source task inputs for later task planning\n"
        "- coordination_plan: detail confirmation, build dispatch, and testing feedback strategy\n"
        "- risks: planning risks and items to refine later\n\n"
        "Keep ids stable and reuse ids from RequirementSpec whenever possible. RequirementSpec:\n"
        f"{json.dumps(requirement_spec, ensure_ascii=False)}"
    )


def _invoke_live_chat_model(
    requirement_spec: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> str:
    active_settings = settings or Settings.from_env()
    result = create_chat_model(active_settings).invoke(_planning_prompt(requirement_spec))
    content = getattr(result, "content", "")
    return content if isinstance(content, str) else str(content)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort parser for model JSON without trusting formatting."""

    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def plan_project_with_chat_model(
    requirement_spec: dict[str, Any],
) -> dict[str, Any]:
    """Use a direct chat-model call to produce a ProjectPlan."""

    settings = Settings.from_env()
    agent_note = _invoke_live_chat_model(requirement_spec, settings=settings)
    planning_source = "direct_chat_model"

    plan = create_project_plan(
        requirement_spec,
        agent_note=agent_note,
        planning_source=planning_source,
        agent_plan=_extract_json_object(agent_note),
    )
    plan["planned_by"] = {
        "agent": "chat-model",
        "mode": "direct",
        "model": settings.model_name,
        "source": planning_source,
    }
    return plan
