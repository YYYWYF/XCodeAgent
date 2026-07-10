from __future__ import annotations

import json
import time
from typing import Any

from app.agents.model_factory import create_chat_model

from app.config import Settings
from app.services.page_detail_plan import (
    create_data_source_detail_plan,
    create_page_detail_plan,
)
from app.utils.model_output import extract_json_object


def _page_design_prompt(
    project_plan: dict[str, Any],
    confirmed_page_spec: dict[str, Any],
) -> str:
    return (
        "You are the page-design model for an app-generation workflow.\n"
        "This is a design-only boundary. Do not call tools, do not call subagents, "
        "do not delegate tasks, and do not generate or modify code.\n"
        "Create a detailed page design from the user-confirmed PageSpec.\n"
        "Return only one complete JSON object without markdown fences or commentary. It must include "
        "page_goal, basic_layout, interactions, data_sources, permissions, page_dependencies, and "
        "acceptance_criteria. Treat PageSpec.user_confirmation_note as the latest user feedback and "
        "let it override conflicting defaults.\n"
        "The ProjectPlan is only context for API contracts, data sources, and dependencies.\n"
        "The PageSpec is the source of truth for page goal, layout, interactions, data sources, and permissions.\n\n"
        "Pay special attention to ProjectPlan.api_contracts and ProjectPlan.page_data_dependencies; "
        "the page design must not invent incompatible APIs or undeclared page/data dependencies.\n\n"
        f"Confirmed PageSpec:\n{json.dumps(confirmed_page_spec, ensure_ascii=False)}\n\n"
        f"ProjectPlan context:\n{json.dumps(project_plan, ensure_ascii=False)}"
    )


def _invoke_live_chat_model(
    project_plan: dict[str, Any],
    confirmed_page_spec: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> str:
    active_settings = settings or Settings.from_env()
    result = create_chat_model(active_settings).invoke(
        _page_design_prompt(project_plan, confirmed_page_spec)
    )
    content = getattr(result, "content", result)
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", item)) if isinstance(item, dict) else str(item)
            for item in content
        )
    return content if isinstance(content, str) else str(content)


def _fallback_model_note(error: Exception) -> str:
    return (
        "页面设计模型调用失败，已降级使用 ProjectPlan 与用户确认的 "
        f"PageSpec 生成确定性页面详细计划。错误：{type(error).__name__}: {error}"
    )


def design_page_with_chat_model(
    project_plan: dict[str, Any],
    confirmed_page_spec: dict[str, Any],
) -> dict[str, Any]:
    """Use a direct chat-model call to create a page detail plan."""

    settings = Settings.from_env()
    design_source = "direct_chat_model"
    fallback_error: Exception | None = None
    for attempt in range(2):
        try:
            agent_note = _invoke_live_chat_model(
                project_plan,
                confirmed_page_spec,
                settings=settings,
            )
            break
        except Exception as exc:
            fallback_error = exc
            if attempt == 0:
                time.sleep(0.8)
                continue
            agent_note = _fallback_model_note(exc)
            design_source = "deterministic_fallback_after_chat_model_error"

    detail_plan = create_page_detail_plan(
        project_plan,
        confirmed_page_spec,
        agent_note=agent_note,
        agent_detail_plan=extract_json_object(agent_note),
    )
    detail_plan["designed_by"] = {
        "agent": "chat-model",
        "mode": "direct",
        "model": settings.model_name,
        "source": design_source,
    }
    detail_plan["design_source"] = design_source
    if fallback_error is not None and design_source != "direct_chat_model":
        detail_plan["design_error"] = {
            "type": type(fallback_error).__name__,
            "message": str(fallback_error),
        }
    return detail_plan


def _data_source_design_prompt(
    project_plan: dict[str, Any],
    data_source_id: str,
    user_request: str,
) -> str:
    return (
        "You are the Main Agent for an app-generation workflow. Return only one complete JSON "
        "object for the selected data source design, without markdown fences or commentary. "
        "Include schema, entities, api_contracts, dependent_pages, seed_strategy, and "
        "acceptance_criteria. The latest user feedback overrides conflicting ProjectPlan defaults. "
        "Do not generate code or call tools.\n\n"
        f"Selected data source id: {data_source_id}\n"
        f"Latest user feedback:\n{user_request}\n\n"
        f"ProjectPlan context:\n{json.dumps(project_plan, ensure_ascii=False)}"
    )


def design_data_source_with_chat_model(
    project_plan: dict[str, Any],
    data_source_id: str,
    user_request: str,
) -> dict[str, Any]:
    settings = Settings.from_env()
    result = create_chat_model(settings).invoke(
        _data_source_design_prompt(project_plan, data_source_id, user_request)
    )
    content = getattr(result, "content", "")
    agent_note = content if isinstance(content, str) else str(content)
    detail_plan = create_data_source_detail_plan(
        project_plan,
        data_source_id,
        user_request=user_request,
        agent_detail_plan=extract_json_object(agent_note),
    )
    detail_plan["agent_note"] = agent_note
    detail_plan["designed_by"] = {
        "agent": "chat-model",
        "mode": "direct",
        "model": settings.model_name,
        "source": "direct_chat_model",
    }
    return detail_plan
