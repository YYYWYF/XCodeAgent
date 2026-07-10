from __future__ import annotations

import json
from typing import Any

from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.project_plan import create_project_plan
from app.utils.model_output import extract_json_object


def _planning_prompt(
    requirement_spec: dict[str, Any],
    existing_plan: dict[str, Any] | None = None,
) -> str:
    revision_context = (
        "Update the existing ProjectPlan using planning_adjustment_request from the RequirementSpec. "
        "The latest user feedback overrides conflicting older plan content. Return the complete updated "
        "plan, including full page, data source, and API lists; omitted items are treated as removed.\n"
        f"Existing ProjectPlan:\n{json.dumps(existing_plan, ensure_ascii=False)}\n\n"
        if existing_plan
        else "Create a new complete ProjectPlan.\n"
    )
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
        "- coordination_plan: stages with owner, strategy, and outputs for detail confirmation, "
        "build dispatch, and testing feedback\n"
        "- risks: planning risks and items to refine later\n\n"
        "Keep ids stable and reuse ids from RequirementSpec whenever possible.\n"
        f"{revision_context}RequirementSpec:\n"
        f"{json.dumps(requirement_spec, ensure_ascii=False)}"
    )


def _invoke_live_chat_model(
    requirement_spec: dict[str, Any],
    *,
    existing_plan: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> str:
    active_settings = settings or Settings.from_env()
    result = create_chat_model(active_settings).invoke(
        _planning_prompt(requirement_spec, existing_plan)
    )
    content = getattr(result, "content", "")
    return content if isinstance(content, str) else str(content)


def plan_project_with_chat_model(
    requirement_spec: dict[str, Any],
    *,
    existing_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use a direct chat-model call to produce a ProjectPlan."""

    settings = Settings.from_env()
    agent_note = _invoke_live_chat_model(
        requirement_spec,
        existing_plan=existing_plan,
        settings=settings,
    )
    planning_source = "direct_chat_model"

    plan = create_project_plan(
        requirement_spec,
        agent_note=agent_note,
        planning_source=planning_source,
        agent_plan=extract_json_object(agent_note),
        authoritative_agent_plan=True,
    )
    plan["planned_by"] = {
        "agent": "chat-model",
        "mode": "direct",
        "model": settings.model_name,
        "source": planning_source,
    }
    return plan


def revise_project_plan_with_chat_model(
    existing_plan: dict[str, Any],
    user_feedback: str,
) -> dict[str, Any]:
    requirement_spec = {
        "version": existing_plan.get("requirement_spec_version", "0.1.0"),
        "app_info": {
            "name": existing_plan.get("app", {}).get("name", "未命名应用"),
            "summary": existing_plan.get("app", {}).get("summary", user_feedback),
            "target": existing_plan.get("requirements_overview", {}).get(
                "target",
                "生成一个可在本地运行的前后端应用工程。",
            ),
        },
        "requirements_overview": existing_plan.get("requirements_overview", {}),
        "user_roles": existing_plan.get("permission_model", {}).get("roles")
        or existing_plan.get("requirements_overview", {}).get("roles", []),
        "feature_modules": existing_plan.get("requirements_overview", {}).get(
            "modules",
            [],
        ),
        "pages": existing_plan.get("frontend_pages", []),
        "data_sources": existing_plan.get("data_sources", []),
        "business_flows": existing_plan.get("business_flows", []),
        "acceptance_criteria": existing_plan.get("acceptance_criteria", []),
        "planning_adjustment_request": user_feedback,
    }
    revised = plan_project_with_chat_model(
        requirement_spec,
        existing_plan=existing_plan,
    )
    revised["planning_source"] = "direct_chat_model_revision"
    revised["confirmation_status"] = "pending_user_confirmation"
    return revised
