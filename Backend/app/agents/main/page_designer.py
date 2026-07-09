from __future__ import annotations

import json
import time
from typing import Any

from app.config import Settings
from app.agents.model_factory import create_chat_model
from app.services.page_detail_plan import create_page_detail_plan


def _page_design_prompt(
    project_plan: dict[str, Any],
    confirmed_page_spec: dict[str, Any],
) -> str:
    return (
        "You are the Main Agent for an app-generation workflow.\n"
        "This is a design-only boundary. Do not call tools, do not call subagents, "
        "do not delegate tasks, and do not generate or modify code.\n"
        "Create a detailed page design from the user-confirmed PageSpec.\n"
        "The ProjectPlan is only context for API contracts, data sources, and dependencies.\n"
        "The PageSpec is the source of truth for page goal, layout, interactions, data sources, and permissions.\n\n"
        "Pay special attention to ProjectPlan.api_contracts and ProjectPlan.page_data_dependencies; "
        "the page design must not invent incompatible APIs or undeclared page/data dependencies.\n\n"
        f"Confirmed PageSpec:\n{json.dumps(confirmed_page_spec, ensure_ascii=False)}\n\n"
        f"ProjectPlan context:\n{json.dumps(project_plan, ensure_ascii=False)}"
    )


def _invoke_live_main_agent(
    project_plan: dict[str, Any],
    confirmed_page_spec: dict[str, Any],
    *,
    workspace: str | None = None,
) -> str:
    settings = Settings.from_env()
    result = create_chat_model(settings).invoke(
        _page_design_prompt(project_plan, confirmed_page_spec)
    )
    content = getattr(result, "content", result)
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", item)) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)


def _fallback_agent_note(error: Exception) -> str:
    return (
        "Main Agent 页面详细设计调用失败，已降级使用 ProjectPlan 与用户确认的 "
        f"PageSpec 生成确定性页面详细计划。错误：{type(error).__name__}: {error}"
    )


def design_page_with_main_agent(
    project_plan: dict[str, Any],
    confirmed_page_spec: dict[str, Any],
    *,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Use the live Main Agent boundary to create a page detail plan."""

    settings = Settings.from_env()
    design_source = "main_agent_live"
    fallback_error: Exception | None = None
    for attempt in range(2):
        try:
            agent_note = _invoke_live_main_agent(
                project_plan,
                confirmed_page_spec,
            )
            break
        except Exception as exc:
            fallback_error = exc
            if attempt == 0:
                time.sleep(0.8)
                continue
            agent_note = _fallback_agent_note(exc)
            design_source = "deterministic_fallback_after_main_agent_error"

    detail_plan = create_page_detail_plan(
        project_plan,
        confirmed_page_spec,
        agent_note=agent_note,
    )
    detail_plan["designed_by"] = {
        "agent": "main-agent",
        "mode": "live",
        "model": settings.model_name,
        "source": design_source,
    }
    detail_plan["design_source"] = design_source
    if fallback_error is not None and design_source != "main_agent_live":
        detail_plan["design_error"] = {
            "type": type(fallback_error).__name__,
            "message": str(fallback_error),
        }
    return detail_plan
