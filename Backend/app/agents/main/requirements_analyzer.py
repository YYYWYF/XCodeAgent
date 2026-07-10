from __future__ import annotations

import json
from typing import Any

from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.requirement_spec import create_requirement_spec
from app.tools.ask_user import ask_user, extract_ask_user_clarification
from app.utils.model_output import extract_json_object


def _requirements_prompt(
    request: str,
    existing_spec: dict[str, Any] | None = None,
) -> str:
    revision_context = (
        "Revise the existing RequirementSpec using the latest user feedback. "
        "The latest feedback overrides conflicting older requirements. Preserve stable ids for "
        "unchanged items, remove items the user no longer wants, and add newly requested items.\n"
        f"Existing RequirementSpec:\n{json.dumps(existing_spec, ensure_ascii=False)}\n\n"
        if existing_spec
        else "Create a new RequirementSpec from the user request.\n"
    )
    return (
        "You are the requirements model for an app-generation workflow.\n"
        "This is a requirements-only boundary. Do not call subagents, do not delegate tasks, "
        "do not create project plans, and do not generate or modify code.\n"
        "The only tool you may call is ask_user, and only when user input is required.\n"
        "Analyze the user's application request and decide whether the requirement is clear enough "
        "to produce a RequirementSpec.\n"
        "A clear RequirementSpec must cover all of these aspects: 应用信息, 用户角色, 功能模块, "
        "页面清单, 数据源清单, 业务流程, 验收标准.\n"
        "If any aspect is missing, ambiguous, or risky to assume, call the ask_user tool with one to "
        "four focused questions. The questions can be choice, text, or yesno, and you should decide "
        "which questions are necessary from the user's request. After calling ask_user, do not invent "
        "answers and do not continue planning until the user answers.\n"
        "If the requirement is clear, do not call ask_user. Return only one complete JSON object "
        "without markdown fences or commentary. It must include app_info, user_roles, "
        "feature_modules, pages, data_sources, business_flows, acceptance_criteria, and assumptions. "
        "Every role, module, page, data source, and flow must have a stable id and the fields needed "
        "to describe it. The JSON must represent the complete current requirement, not a patch.\n\n"
        f"{revision_context}Latest user request or feedback:\n{request}"
    )


def _invoke_live_chat_model(
    request: str,
    *,
    existing_spec: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    active_settings = settings or Settings.from_env()
    result = (
        create_chat_model(active_settings)
        .bind_tools([ask_user])
        .invoke(_requirements_prompt(request, existing_spec))
    )
    return {"messages": [result]}


def analyze_requirements_with_chat_model(
    request: str,
    existing_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use a direct chat-model call to create RequirementSpec and clarifications."""

    settings = Settings.from_env()
    agent_result = _invoke_live_chat_model(
        request,
        existing_spec=existing_spec,
        settings=settings,
    )
    messages = agent_result.get("messages", [])
    content = getattr(messages[-1], "content", "") if messages else ""
    agent_note = content if isinstance(content, str) else str(content)
    analysis_source = "direct_chat_model"

    agent_spec = extract_json_object(agent_note)
    spec = create_requirement_spec(
        request,
        agent_note=agent_note,
        agent_spec=agent_spec,
        existing_spec=existing_spec,
    )
    clarification = extract_ask_user_clarification(agent_result, spec)
    spec["clarification_questions"] = clarification["questions"]
    spec["assumptions"] = clarification["assumptions"]
    spec["clarification_status"] = clarification["status"]
    spec["unresolved_requirement_dimensions"] = clarification.get(
        "all_unresolved_dimensions", []
    )
    spec["analyzed_by"] = {
        "agent": "chat-model",
        "mode": "direct",
        "model": settings.model_name,
        "source": analysis_source,
    }
    spec["analysis_source"] = analysis_source
    spec["agent_spec_used"] = isinstance(agent_spec, dict)
    clarification["requested_by"] = "chat-model"
    clarification["analysis_source"] = analysis_source
    clarification["analysis_note"] = json.dumps(
        {
            "mode": "direct",
            "source": analysis_source,
            "agent_note": agent_note,
        },
        ensure_ascii=False,
    )
    return {
        "requirement_spec": spec,
        "clarification": clarification,
    }
