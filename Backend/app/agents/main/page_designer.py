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
    "data_origin": {
        "source_type": "database|static|external_api",
        "effective_source": {
            "kind": "frontend_mock|third_party|mysql_existing|mysql_new_table|needs_user_confirmation",
            "data_source_id": "string|null",
            "database": "string|null",
            "tables": ["string"],
            "provider": "string|null",
            "endpoint": "string|null",
            "method": "string|null",
            "description": "string",
        },
        "field_mappings": [
            {
                "target_field": "string",
                "source": "string",
                "rule": "string",
            }
        ],
        "differences": [
            {
                "field": "string",
                "expected": "string",
                "actual": "string",
                "resolution_kind": "already_supported|database_change|backend_adaptation|needs_user_confirmation",
                "operation_refs": ["string"],
                "backend_adaptation": None,
            }
        ],
        "database_operations": [
            {
                "id": "string",
                "operation": "create_table|add_column|alter_column_type|alter_column_nullable|alter_column_default",
                "database": "string",
                "table": "string|object",
                "column": "string|null",
                "from": "object|null",
                "to": {
                    "type": "string|null",
                    "nullable": "boolean|null",
                    "default": "any|null",
                    "comment": "string",
                },
                "reason": "string",
                "source_fields": ["string"],
            }
        ],
        "notes": ["string"],
    },
    "operation_semantics": {
        "operation_kind": "read|create|update|delete|action",
        "target_cardinality": "exactly_one|zero_or_one|many|not_applicable",
        "selector": {
            "source": "path|query|request_body|contract|none",
            "fields": ["string"],
        },
        "transaction_required": "boolean",
        "zero_match_behavior": "string",
        "multiple_match_behavior": "string",
        "success_status_code": "number",
        "side_effect": "none|insert|update|delete|custom",
    },
    "risks": ["string"],
}


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
        f"Current page context:\n{json.dumps(page_context, ensure_ascii=False)}"
    )


def _invoke_live_chat_model(
    project_plan: dict[str, Any],
    page_context: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> str:
    active_settings = settings or Settings.from_env()
    result = create_chat_model(active_settings).bind(
        max_tokens=active_settings.default_max_tokens
    ).invoke(
        _page_design_prompt(project_plan, page_context)
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


def _endpoint_decision_prompt(
    endpoint_context: dict[str, Any],
    user_request: str,
) -> str:
    """构造 EndpointDetail 第一步的唯一语义决策提示词。"""

    formal_schema = json.dumps(ENDPOINT_DECISION_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
    return (
        "You are the endpoint-decision model for an app-generation workflow.\n"
        "This is a design-only boundary. Do not call tools, do not call subagents, "
        "and do not generate or modify code.\n"
        "This is step 1 of EndpointDetail design. Decide the implementation semantics for "
        "exactly one API endpoint. The API contract is the "
        "source of truth for method, path, parameters, request schema and response schema. "
        "Do not add new endpoints or change the contract. Return only one JSON object without "
        "markdown fences or commentary. The object is the only semantic source used to compose "
        "processing logic and acceptance criteria. It must match this formal schema exactly; "
        "replace the sample type strings with concrete design content and keep every key present:\n"
        f"{formal_schema}\n"
        "data_origin must be compact: include exactly one effective_source object for the "
        "actual source, field_mappings for known existing target-to-source mappings, structured "
        "differences for every schema/field gap, database_operations for confirmed schema changes, "
        "and notes for concise assumptions. Every difference must use resolution_kind; "
        "database_change must reference complete database_operations by id, backend_adaptation "
        "must be an object with strategy (type_conversion/computed/default_value/not_persisted), "
        "value, temporary, and description; all other resolution kinds must set it to null. "
        "needs_user_confirmation must not contain an executable "
        "operation. mysql_existing may add or alter columns on an existing table. Never infer a "
        "database operation from prose and never map a missing column as an existing field_mapping. "
        "operation_refs is allowed only on database_change differences; every other "
        "resolution_kind (already_supported, backend_adaptation, needs_user_confirmation) "
        "must keep operation_refs empty ([]). "
        "Do not output parallel "
        "third_party/mysql_existing/mysql_new_table branches, do not output applicable=false "
        "objects, and do not store user-facing questions inside data_origin. "
        "When endpoint_context.database_context.status is completed, the inspected database "
        "facts are authoritative and you must produce the concrete database design directly in "
        "effective_source.kind; needs_user_confirmation must not appear in that state. If the "
        "target table already exists in database_context.tables, set effective_source.kind to "
        "mysql_existing, map contract fields to its columns in field_mappings, and express every "
        "missing or insufficient column as a database_change difference that references a "
        "complete add_column/alter_column_type/alter_column_nullable/alter_column_default "
        "database_operation; fields whose values are computed or transformed by the backend are "
        "backend_adaptation differences with operation_refs []. "
        "If the target table is absent or database_context.database_exists is false, set "
        "effective_source.kind to mysql_new_table and design the table directly from the API "
        "contract: provide one complete create_table database_operation whose table object "
        "includes name, comment, columns (name/type/nullable/default/comment), primary_key, "
        "indexes and foreign_keys, and cover every contract field in "
        "field_mappings/differences. Use database_context.database as the target database name. "
        "For mysql_new_table, also declare the table creation itself as exactly one "
        "database_change difference with operation_refs referencing the create_table operation "
        "id and backend_adaptation null (use the new table name as its field); columns defined "
        "by create_table belong in field_mappings, and fields whose values are produced by "
        "backend logic or database defaults (server-generated id, computed values, timestamps) "
        "are backend_adaptation differences with operation_refs []. "
        "When database_context is skipped or failed, do not invent inspected tables; continue "
        "from the API contract and record concrete unresolved schema gaps in "
        "data_origin.differences. "
        "data_origin.source_type is the immutable ProjectPlan data source category and must be "
        "database, static, or external_api; keep the category unchanged. Only when "
        "database_context is skipped or failed and the concrete implementation is genuinely "
        "unclear may you set effective_source.kind to needs_user_confirmation; when "
        "database_context.status is completed, effective_source.kind must be mysql_existing or "
        "mysql_new_table, never needs_user_confirmation. "
        "operation_semantics must express contract behavior once, without implementation prose. "
        "For a single-resource endpoint, target_cardinality and multiple_match_behavior must make "
        "the single-resource boundary explicit. selector.fields must only use contract parameters "
        "or request fields. success_status_code must follow the API contract when declared. "
        "Even when data_origin needs confirmation, still describe the endpoint's intended contract "
        "semantics; the workflow will stop before composing executable detail fields. "
        "If the ProjectPlan data source type is static, set data_origin.source_type to \"static\" "
        "and effective_source.kind to \"frontend_mock\". It uses the frontend in-memory data module, "
        "has no database operations, and does not create a real backend endpoint. Never emit mock as "
        "a formal source_type or effective_source.kind.\n\n"
        f"Latest user feedback:\n{user_request}\n\n"
        f"Endpoint context:\n{json.dumps(endpoint_context, ensure_ascii=False)}"
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
