from __future__ import annotations

import json
from typing import Any

from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.requirement_spec import create_requirement_spec
from app.tools.ask_user import ask_user, extract_ask_user_clarification


def _requirements_prompt(request: str) -> str:
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
        "If the requirement is clear, do not call ask_user. Return a concise analysis note that "
        "summarizes the seven aspects and any safe assumptions.\n\n"
        f"User request:\n{request}"
    )


def _invoke_live_chat_model(
    request: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    active_settings = settings or Settings.from_env()
    result = (
        create_chat_model(active_settings)
        .bind_tools([ask_user])
        .invoke(_requirements_prompt(request))
    )
    return {"messages": [result]}


def analyze_requirements_with_chat_model(request: str) -> dict[str, Any]:
    """Use a direct chat-model call to create RequirementSpec and clarifications."""

    settings = Settings.from_env()
    agent_result = _invoke_live_chat_model(request, settings=settings)
    messages = agent_result.get("messages", [])
    content = getattr(messages[-1], "content", "") if messages else ""
    agent_note = content if isinstance(content, str) else str(content)
    analysis_source = "direct_chat_model"

    spec = create_requirement_spec(request, agent_note=agent_note)
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
