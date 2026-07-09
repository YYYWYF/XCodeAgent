from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.services.requirement_spec import create_requirement_spec
from app.tools.ask_user import extract_ask_user_clarification


def _requirements_prompt(request: str) -> str:
    return (
        "You are the Main Agent for an app-generation workflow.\n"
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


def _invoke_live_main_agent(
    request: str,
    *,
    workspace: str | None = None,
) -> dict[str, Any]:
    # Lazy imports keep Deep Agent construction at this live execution boundary.
    from app.agents import create_agent_bundle

    return create_agent_bundle(workspace).main.invoke(
        {"messages": [{"role": "user", "content": _requirements_prompt(request)}]}
    )


def analyze_requirements_with_main_agent(
    request: str,
    *,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Use the live Main Agent boundary to create RequirementSpec and clarifications."""

    settings = Settings.from_env()
    agent_result = _invoke_live_main_agent(request, workspace=workspace)
    from app.graph.nodes.common import last_agent_text

    agent_note = last_agent_text(agent_result)
    analysis_source = "main_agent_live"

    spec = create_requirement_spec(request, agent_note=agent_note)
    clarification = extract_ask_user_clarification(agent_result, spec)
    spec["clarification_questions"] = clarification["questions"]
    spec["assumptions"] = clarification["assumptions"]
    spec["clarification_status"] = clarification["status"]
    spec["unresolved_requirement_dimensions"] = clarification.get(
        "all_unresolved_dimensions", []
    )
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
