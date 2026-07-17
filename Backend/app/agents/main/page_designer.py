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


class PageDependencyGapError(ValueError):
    """表示页面设计需要 ProjectPlan 尚未声明的接口或跳转依赖。"""


def _page_design_prompt(
    project_plan: dict[str, Any],
    page_context: dict[str, Any],
) -> str:
    return (
        "You are the page-design model for an app-generation workflow.\n"
        "This is a design-only boundary. Do not call tools, do not call subagents, "
        "do not delegate tasks, and do not generate or modify code.\n"
        "Create a detailed page design for the current page extracted from ProjectPlan.\n"
        "The references.permissions, references.endpoint_dependencies, and "
        "references.navigation_targets in page_context are immutable ProjectPlan projections. "
        "Do not add, remove, or replace any dependency. If the page needs a missing API or navigation "
        "target, return dependency_gap with a concise reason and no invented endpoint.\n"
        "Return only one complete JSON object without markdown fences or commentary. It must include "
        "page_goal, basic_layout, layout_design, state_feedback, operation_interactions, page_navigation, "
        "api_dependencies, "
        "response_bindings, permissions, and acceptance_criteria. "
        "layout_design must describe overall layout, business regions, primary content presentation, "
        "operation entry positions, and responsive/information-density strategy. Do not model loading, "
        "empty, error, toast, validation, or confirmation feedback as layout areas; put those in "
        "state_feedback or operation_interactions. "
        "state_feedback must describe loading, empty, error, ready, and operation feedback behavior "
        "with related feedback components such as Spin, Empty, Alert, Message, or Modal.confirm. "
        "operation_interactions must describe major in-page behavior such as query, create, update, "
        "delete, submit, cancel, refresh, batch actions, and navigation clicks, with the related "
        "endpoint_id when an API is used. api_dependencies must select the page's actual APIs from "
        "ProjectPlan.api_contracts and include endpoint_id, usage, trigger, "
        "required_for_initial_load, and binds_to. page_navigation must describe internal page jumps "
        "and the target page/path when known. Every response_binding must contain endpoint_id, "
        "source_path, and page_field; endpoint_id must come from selected api_dependencies and "
        "source_path must exist in that endpoint's response schema. Do not add fields, schemas, "
        "endpoints, or data sources. "
        "Describe page data access through concrete API endpoints instead of underlying data sources. "
        "If existing API contracts cannot support a required page interaction, state the gap in "
        "acceptance_criteria or risks instead of inventing a new endpoint.\n"
        "The page_context is the source of truth for the current page goal, layout, immutable references, "
        "related-page summaries, and selected endpoint contract context.\n\n"
        f"Current page context:\n{json.dumps(page_context, ensure_ascii=False)}"
    )


def _invoke_live_chat_model(
    project_plan: dict[str, Any],
    page_context: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> str:
    active_settings = settings or Settings.from_env()
    result = create_chat_model(active_settings).invoke(
        _page_design_prompt(project_plan, page_context)
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
        f"页面上下文生成确定性页面详细计划。错误：{type(error).__name__}: {error}"
    )


def design_page_with_chat_model(
    project_plan: dict[str, Any],
    page_context: dict[str, Any],
) -> dict[str, Any]:
    """Use a direct chat-model call to create a page detail plan."""

    settings = Settings.from_env()
    design_source = "direct_chat_model"
    fallback_error: Exception | None = None
    for attempt in range(2):
        try:
            agent_note = _invoke_live_chat_model(
                project_plan,
                page_context,
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

    agent_detail_plan = extract_json_object(agent_note)
    dependency_gap = agent_detail_plan.get("dependency_gap") if isinstance(agent_detail_plan, dict) else None
    if dependency_gap:
        message = (
            str(dependency_gap.get("message") or dependency_gap.get("reason") or "")
            if isinstance(dependency_gap, dict)
            else str(dependency_gap)
        )
        raise PageDependencyGapError(message or "页面设计需要修订 ProjectPlan 依赖。")
    detail_plan = create_page_detail_plan(
        project_plan,
        page_context,
        agent_note=agent_note,
        agent_detail_plan=agent_detail_plan,
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
        "Include schema_refs, entities, dependent_pages, seed_strategy, and acceptance_criteria. "
        "ProjectPlan.api_contracts is the only source of field definitions. Do not return schema, "
        "do not add fields, and do not alter api_contracts. The latest user feedback overrides "
        "non-contract planning defaults. "
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
