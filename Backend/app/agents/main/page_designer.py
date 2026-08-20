from __future__ import annotations

import json
import time
from typing import Any

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model

from app.config import Settings
from app.services.page_detail_plan import (
    compose_endpoint_detail_from_decision,
    create_page_detail_plan,
)
from app.utils.model_output import extract_json_object


ENDPOINT_DECISION_OUTPUT_SCHEMA: dict[str, Any] = {
    "operation_semantics": {
        "operation_kind": "read|create|update|delete|action",
        "target_cardinality": "exactly_one|zero_or_one|many|not_applicable",
        "selector": {
            "source": "path|query|request_body|contract|none",
            "fields": ["<contract parameter or request field name>"],
        },
        "transaction_required": False,
        "zero_match_behavior": "<concise Chinese behavior>",
        "multiple_match_behavior": "<concise Chinese behavior>",
        "success_status_code": 200,
        "side_effect": "none|insert|update|delete|custom",
    },
    "risks": ["<concise Chinese risk>"],
}


class PageDependencyGapError(ValueError):
    """表示页面设计需要 ProjectPlan 尚未声明的接口或跳转依赖。"""


def _page_design_prompt(
    project_plan: dict[str, Any],
    page_context: dict[str, Any],
    user_request: str = "",
) -> str:
    return (
        "You are the page-design model for an app-generation workflow.\n"
        "This is a design-only boundary. Do not call tools, do not call subagents, "
        "do not delegate tasks, and do not generate or modify code.\n"
        "Create a detailed page design for the current page extracted from ProjectPlan.\n"
        "The references.permissions, references.endpoint_dependencies, and "
        "references.navigation_targets in page_context are immutable ProjectPlan projections. "
        "Do not add, remove, or replace any dependency. Do NOT return dependency_gap — "
        "always produce a complete page design. If the page describes interactions (create, update, "
        "delete, etc.) for which no endpoint_id is declared in api_contracts, describe them as "
        "real UI interactions in operation_interactions but omit the endpoint_id, and note the gap in "
        "acceptance_criteria as \"待补充 API 端点：xxx\".\n"
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
        "The page_context is the source of truth for the current page goal, layout, immutable references, "
        "related-page summaries, and selected endpoint contract context.\n\n"
        f"Latest user feedback (apply only when it preserves the confirmed product scope):\n{user_request}\n\n"
        f"Current page context:\n{json.dumps(page_context, ensure_ascii=False)}"
    )


def _invoke_live_chat_model(
    project_plan: dict[str, Any],
    page_context: dict[str, Any],
    *,
    user_request: str = "",
    settings: Settings | None = None,
) -> str:
    active_settings = settings or Settings.from_env()
    result = create_chat_model(active_settings).bind(
        max_tokens=active_settings.default_max_tokens
    ).invoke(
        _page_design_prompt(project_plan, page_context, user_request)
    )
    content = getattr(result, "content", result)
    return _coerce_content_text(content) or ""


def _fallback_model_note(error: Exception) -> str:
    return (
        "页面设计模型调用失败，已降级使用项目计划与用户确认的 "
        f"页面上下文生成确定性页面详细计划。错误：{type(error).__name__}: {error}"
    )


def design_page_with_chat_model(
    project_plan: dict[str, Any],
    page_context: dict[str, Any],
    user_request: str = "",
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
                user_request=user_request,
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
        raise PageDependencyGapError(message or "页面设计需要修订项目计划中的依赖。")
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


def _endpoint_decision_input(
    endpoint_context: dict[str, Any],
    user_request: str,
) -> dict[str, Any]:
    """提取接口语义决策真正需要的业务上下文，避免暴露内部定位和数据源信息。"""

    endpoint = (
        endpoint_context.get("endpoint")
        if isinstance(endpoint_context.get("endpoint"), dict)
        else {}
    )
    dependent_pages = (
        endpoint_context.get("dependent_pages")
        if isinstance(endpoint_context.get("dependent_pages"), list)
        else []
    )
    bound_entities = (
        endpoint_context.get("bound_entities")
        if isinstance(endpoint_context.get("bound_entities"), list)
        else []
    )
    return {
        "endpoint_contract": {
            "method": str(endpoint_context.get("method") or "GET").upper(),
            "path": str(endpoint_context.get("path") or ""),
            "summary": str(endpoint_context.get("summary") or ""),
            "parameters": (
                endpoint.get("parameters")
                if isinstance(endpoint.get("parameters"), list)
                else []
            ),
            "request": {
                "type_name": endpoint.get("request_schema_ref"),
                "schema": endpoint_context.get("request_schema"),
            },
            "response": {
                "type_name": endpoint.get("response_schema_ref"),
                "schema": endpoint_context.get("response_schema"),
            },
            "error_codes": endpoint.get("error_responses")
            or endpoint.get("error_codes")
            or [],
        },
        "consumer_scenarios": [
            {
                "page_name": str(page.get("page_name") or ""),
                "usage": str(page.get("usage") or ""),
                "trigger": str(page.get("trigger") or ""),
            }
            for page in dependent_pages
            if isinstance(page, dict)
        ],
        "related_entities": [
            {
                "name": str(entity.get("entity_name") or ""),
                "fields": [
                    {
                        "name": str(field.get("name") or ""),
                        "label": str(field.get("label") or ""),
                        "type": str(field.get("type") or ""),
                        "required": bool(field.get("required")),
                    }
                    for field in (
                        entity.get("fields")
                        if isinstance(entity.get("fields"), list)
                        else []
                    )
                    if isinstance(field, dict)
                ],
            }
            for entity in bound_entities
            if isinstance(entity, dict)
        ],
        "latest_user_feedback": user_request.strip(),
    }


def _endpoint_decision_prompt(
    endpoint_context: dict[str, Any],
    user_request: str,
) -> str:
    """构造职责、输入和输出边界清晰的接口语义决策提示词。"""

    output_template = json.dumps(
        ENDPOINT_DECISION_OUTPUT_SCHEMA,
        ensure_ascii=False,
        indent=2,
    )
    decision_input = json.dumps(
        _endpoint_decision_input(endpoint_context, user_request),
        ensure_ascii=False,
        indent=2,
    )
    return (
        "<role>\n"
        "You are the endpoint behavior decision model in an app-generation workflow. "
        "You make one closed behavioral decision for exactly one fixed API endpoint.\n"
        "</role>\n\n"
        "<goal>\n"
        "Fill in the behavioral semantics that the API contract does not state directly but "
        "that deterministic processing logic and acceptance criteria require. Do not design or "
        "modify the API contract, produce the final EndpointDetail, implement code, call tools, "
        "or delegate work.\n"
        "</goal>\n\n"
        "<input_explanation>\n"
        "- endpoint_contract is the immutable source of truth for the method, path, parameters, "
        "request type, response type, schemas, and error behavior.\n"
        "- request.type_name and response.type_name are contract-defined business type names. "
        "Interpret each together with its schema; never rename, replace, or extend either type.\n"
        "- consumer_scenarios only explain why pages use this endpoint. They may clarify intent "
        "but may not add capabilities, fields, parameters, or endpoints.\n"
        "- related_entities are the business objects involved in this endpoint. Their confirmed "
        "fields define the allowed business-field boundary; they do not describe storage or "
        "source implementation.\n"
        "- latest_user_feedback may refine an undecided behavior only when it remains compatible "
        "with endpoint_contract and related_entities.\n"
        "</input_explanation>\n\n"
        "<output_field_definitions>\n"
        "- operation_kind: the endpoint's primary business operation. Use action only for a "
        "non-CRUD business action.\n"
        "- target_cardinality: the number of business targets one request may process or return. "
        "exactly_one requires one target, zero_or_one permits an optional singleton, many permits "
        "a collection or batch, and not_applicable means target count has no business meaning.\n"
        "- selector.source: where the target locator or filter comes from. contract means the "
        "fixed route semantics determine the target without a named input field; none means the "
        "operation does not select an existing target.\n"
        "- selector.fields: only the contract parameter names or request-field names that actually "
        "locate or filter targets. Use an empty array for contract or none.\n"
        "- transaction_required: whether the business operation must complete atomically as one "
        "all-or-nothing action, without naming a storage technology.\n"
        "- zero_match_behavior: the externally observable contract behavior when no target matches.\n"
        "- multiple_match_behavior: the externally observable contract behavior when matches "
        "exceed the selected target cardinality.\n"
        "- success_status_code: the integer HTTP status for success. Follow an explicitly declared "
        "contract status; otherwise use 201 for single-resource creation, 204 for DELETE without "
        "a response schema, and 200 for all other cases.\n"
        "- side_effect: the conceptual change to business resource state, not an implementation "
        "procedure.\n"
        "- risks: concise Chinese strings for material contract ambiguity, missing entity business "
        "fields, or assumptions requiring confirmation. Return [] when none exist.\n"
        "</output_field_definitions>\n\n"
        "<decision_rules>\n"
        "Keep the contract unchanged and do not add schemas, fields, entities, or endpoints. For "
        "a single-resource endpoint, make zero-match and multiple-match behavior explicit. Do not "
        "treat response envelope or control fields such as items, total, success, or message as "
        "missing entity fields. Express each behavior once and do not include implementation prose "
        "or source-specific operations.\n"
        "</decision_rules>\n\n"
        f"<input>\n{decision_input}\n</input>\n\n"
        "<output_requirement>\n"
        "Return exactly one JSON object with every key shown below. Use the listed enum values "
        "exactly, use real JSON booleans and integers, replace descriptive placeholders with "
        "concrete content, and include no markdown fences, commentary, or additional keys.\n"
        f"{output_template}\n"
        "</output_requirement>"
    )


def design_endpoint_with_chat_model(
    project_plan: dict[str, Any],
    endpoint_context: dict[str, Any],
    user_request: str = "",
) -> dict[str, Any]:
    """先生成唯一 EndpointDecision，再确定性组装单个 EndpointDetail。"""

    settings = Settings.from_env()
    design_source = "direct_chat_model"
    try:
        result = create_chat_model(settings).bind(
            max_tokens=settings.default_max_tokens
        ).invoke(
            _endpoint_decision_prompt(endpoint_context, user_request)
        )
        content = getattr(result, "content", "")
        model_output = _coerce_content_text(content) or ""
        endpoint_decision = extract_json_object(model_output)
    except Exception as exc:
        raise RuntimeError(
            f"接口详细设计生成失败：{type(exc).__name__}: {exc}"
        ) from exc
    if not endpoint_decision:
        raise ValueError("接口决策模型未返回可解析的 JSON 设计内容。")
    detail_plan = compose_endpoint_detail_from_decision(
        project_plan,
        endpoint_context,
        endpoint_decision,
        user_request=user_request,
    )
    detail_plan["designed_by"] = {
        "agent": "chat-model",
        "mode": "direct",
        "model": settings.model_name,
        "source": design_source,
    }
    detail_plan["design_source"] = design_source
    return detail_plan
