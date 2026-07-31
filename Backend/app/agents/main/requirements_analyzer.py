from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.requirement_spec import (
    create_requirement_spec,
    merge_clarification_answers_into_spec,
)
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
    clarification_policy = (
        "Before asking the user, silently audit every required aspect together, including the "
        "information needed to derive API contracts, page inventory, data-source inventory, business "
        "flows, roles, and acceptance criteria. In each clarification turn, batch every material missing "
        "or ambiguous item into one to four focused questions. An application name and a broad scenario "
        "alone are not sufficient when roles, core tasks, page boundaries, data sources, permissions, or "
        "the primary business flow cannot be inferred safely. Ask only about gaps that would materially "
        "change the product design; use explicit assumptions for secondary details when safe. Do not "
        "ask open-ended follow-up questions such as whether there are more roles, pages, "
        "or optional features after the user has answered a prior clarification turn.\n"
    )
    followup_policy = (
        "The latest request contains answers to a previous clarification turn. Treat these answers as "
        "the user's confirmation for the asked dimensions. Merge them into a complete RequirementSpec "
        "now. Do not call ask_user again for the same dimensions, and do not ask optional 'any other "
        "roles/pages/features' questions. Use explicit assumptions for optional details that remain "
        "unspecified.\n"
        if existing_spec
        and existing_spec.get("confirmation_status") == "pending_user_input"
        else ""
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
        f"{clarification_policy}"
        f"{followup_policy}"
        "When asking, questions can be choice, text, or yesno. For every choice question, first decide "
        "whether the options are mutually exclusive. Set multiSelect=true for independently combinable "
        "capabilities or requirements (for example search, filtering, import/export, and pagination); "
        "do not turn their combinations into several single-choice options. Set multiSelect=false only "
        "for genuine either-or decisions. After calling ask_user, do not invent answers and do not "
        "continue planning until the user answers.\n"
        "If the requirement is clear, do not call ask_user. Return only one complete JSON object "
        "without markdown fences or commentary. It must include app_info, user_roles, "
        "feature_modules, pages, data_sources, business_flows, acceptance_criteria, and assumptions. "
        "Every role, module, data source, and flow must have a stable id. Every page must have a "
        "stable pageId, name, unique path, module_id, and description. Use '/' only for the single "
        "home/dashboard page; all other pages must have business routes derived from their pageId, "
        "such as '/employees-list' or '/onboarding-form'. Never return multiple pages with path '/'. "
        "The JSON must represent the complete current requirement, not a patch.\n\n"
        f"{revision_context}Latest user request or feedback:\n{request}"
    )


def _invoke_live_chat_model(
    request: str,
    *,
    existing_spec: dict[str, Any] | None = None,
    settings: Settings | None = None,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """调用绑定澄清工具的需求模型，可选流式吐词。"""

    active_settings = settings or Settings.from_env()
    runnable = create_chat_model(active_settings).bind_tools([ask_user])
    if on_token is None:
        result = runnable.invoke(_requirements_prompt(request, existing_spec))
        return {"messages": [result]}

    accumulated_text = ""
    merged_chunk: AIMessageChunk | None = None
    for chunk in runnable.stream(_requirements_prompt(request, existing_spec)):
        if isinstance(chunk, AIMessageChunk):
            token = chunk.content
            if isinstance(token, str) and token:
                accumulated_text += token
                on_token(token)
            merged_chunk = chunk if merged_chunk is None else merged_chunk + chunk
    if merged_chunk is None:
        return {"messages": []}
    final_tool_calls = getattr(merged_chunk, "tool_calls", None) or []
    final = AIMessage(
        content=accumulated_text,
        tool_calls=final_tool_calls
        if hasattr(merged_chunk, "tool_calls")
        else None,
        id=merged_chunk.id if hasattr(merged_chunk, "id") else None,
    )
    return {"messages": [final]}


def analyze_requirements_with_chat_model(
    request: str,
    existing_spec: dict[str, Any] | None = None,
    *,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """直接调用需求模型生成 RequirementSpec，并在关键需求不足时请求澄清。"""

    settings = Settings.from_env()
    agent_result = _invoke_live_chat_model(
        request,
        existing_spec=existing_spec,
        settings=settings,
        on_token=on_token,
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
    if _is_clarification_followup(existing_spec):
        spec = merge_clarification_answers_into_spec(spec, request)
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


def _is_clarification_followup(existing_spec: dict[str, Any] | None) -> bool:
    return bool(
        isinstance(existing_spec, dict)
        and existing_spec.get("confirmation_status") == "pending_user_input"
    )
