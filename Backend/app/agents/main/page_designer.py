from __future__ import annotations

import json
import time
from typing import Any

from app.agents.model_factory import create_chat_model

from app.config import Settings
from app.services.page_detail_plan import (
    create_endpoint_detail_plan,
    create_page_detail_plan,
)
from app.utils.model_output import extract_json_object


ENDPOINT_DETAIL_OUTPUT_SCHEMA: dict[str, Any] = {
    "data_usage": {
        "served_pages": [
            {
                "page_id": "string",
                "page_label": "string",
                "usage": "string",
            }
        ],
        "purpose": "string",
        "served_business": "string",
        "consumer": "string",
    },
    "data_origin": {
        "source_type": "third_party|mysql_existing|mysql_new_table|needs_user_confirmation",
        "third_party": {
            "applicable": "boolean",
            "provider": "string|null",
            "endpoint": "string|null",
            "method": "string|null",
            "request": "object",
            "response": "object",
            "mapping": ["string"],
        },
        "mysql_existing": {
            "applicable": "boolean",
            "database": "MySQL8",
            "tables": [
                {
                    "table_name": "string",
                    "purpose": "string",
                    "fields_used": ["string"],
                }
            ],
        },
        "mysql_new_table": {
            "applicable": "boolean",
            "database": "MySQL8",
            "table_name": "string|null",
            "fields": [
                {
                    "name": "string",
                    "type": "string",
                    "nullable": "boolean",
                    "description": "string",
                }
            ],
            "ddl": "string|null",
        },
        "open_questions": ["string"],
    },
    "interface_design": {
        "restful_style": {
            "compliant": "boolean",
            "method": "string",
            "path": "string",
            "resource": "string",
            "description": "string",
        },
        "request": {
            "path_parameters": ["object"],
            "query_parameters": ["object"],
            "header_parameters": ["object"],
            "request_body": {
                "required": "boolean",
                "schema_ref": "string|null",
                "fields": ["object"],
                "note": "string",
            },
            "file_upload": {
                "required": "boolean",
                "format": "string|null",
                "note": "string",
            },
        },
        "response_format": {
            "status_code": "number",
            "content_type": "string",
            "schema_ref": "string|null",
            "structure": "object",
            "errors": ["object|string"],
        },
    },
    "processing_logic": ["string"],
    "dependent_pages": ["object"],
    "acceptance_criteria": ["string"],
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
    result = create_chat_model(active_settings).bind(
        max_tokens=active_settings.default_max_tokens
    ).invoke(
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


def _endpoint_design_prompt(
    project_plan: dict[str, Any],
    endpoint_context: dict[str, Any],
    user_request: str,
) -> str:
    """构造单个 endpoint 详细设计提示词。"""

    formal_schema = json.dumps(ENDPOINT_DETAIL_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)
    return (
        "You are the endpoint-design model for an app-generation workflow.\n"
        "This is a design-only boundary. Do not call tools, do not call subagents, "
        "and do not generate or modify code.\n"
        "Create a detailed design for exactly one API endpoint. The API contract is the "
        "source of truth for method, path, parameters, request schema and response schema. "
        "Do not add new endpoints or change the contract. Return only one JSON object without "
        "markdown fences or commentary. The JSON object must match this formal schema exactly; "
        "replace the sample type strings with concrete design content and keep every key present:\n"
        f"{formal_schema}\n"
        "data_usage must be an object and explain what the data serves. "
        "data_origin must be an object and explicitly describe third_party, mysql_existing, "
        "and mysql_new_table branches; mark non-applicable branches with applicable=false instead "
        "of omitting them. interface_design must be an object and include restful_style, "
        "request.path_parameters, request.query_parameters, request.header_parameters, "
        "request.request_body, request.file_upload, and response_format. "
        "If data source origin is unclear, set data_origin.source_type to needs_user_confirmation "
        "and list concrete questions in data_origin.open_questions.\n\n"
        f"Latest user feedback:\n{user_request}\n\n"
        f"Endpoint context:\n{json.dumps(endpoint_context, ensure_ascii=False)}\n\n"
        f"ProjectPlan context:\n{json.dumps(project_plan, ensure_ascii=False)}"
    )


def design_endpoint_with_chat_model(
    project_plan: dict[str, Any],
    endpoint_context: dict[str, Any],
    user_request: str = "",
) -> dict[str, Any]:
    """使用直接模型调用生成单个 endpoint 详细设计；失败时交由 Workflow 向前端报错。"""

    settings = Settings.from_env()
    design_source = "direct_chat_model"
    try:
        result = create_chat_model(settings).bind(
            max_tokens=settings.default_max_tokens
        ).invoke(
            _endpoint_design_prompt(project_plan, endpoint_context, user_request)
        )
        content = getattr(result, "content", "")
        model_output = content if isinstance(content, str) else str(content)
        agent_detail_plan = extract_json_object(model_output)
    except Exception as exc:
        raise RuntimeError(
            f"接口详细设计生成失败：{type(exc).__name__}: {exc}"
        ) from exc
    if not agent_detail_plan:
        raise ValueError("接口详细设计模型未返回可解析的 JSON 设计内容。")
    _validate_endpoint_detail_plan(agent_detail_plan)
    detail_plan = create_endpoint_detail_plan(
        project_plan,
        endpoint_context,
        user_request=user_request,
        agent_detail_plan=agent_detail_plan,
    )
    detail_plan["designed_by"] = {
        "agent": "chat-model",
        "mode": "direct",
        "model": settings.model_name,
        "source": design_source,
    }
    detail_plan["design_source"] = design_source
    return detail_plan


def _validate_endpoint_detail_plan(agent_detail_plan: dict[str, Any]) -> None:
    """校验模型返回的 endpoint 详细设计正式结构，失败时阻止写入无效产物。"""

    required_objects = ("data_usage", "data_origin", "interface_design")
    for required_field in required_objects:
        field_value = agent_detail_plan.get(required_field)
        if not isinstance(field_value, dict) or not field_value:
            raise ValueError(f"接口详细设计缺少有效字段：{required_field}")

    data_origin = agent_detail_plan["data_origin"]
    for branch in ("third_party", "mysql_existing", "mysql_new_table"):
        if not isinstance(data_origin.get(branch), dict):
            raise ValueError(f"接口详细设计缺少数据来源分支：data_origin.{branch}")
    if not isinstance(data_origin.get("open_questions"), list):
        raise ValueError("接口详细设计字段类型错误：data_origin.open_questions 必须是数组")

    interface_design = agent_detail_plan["interface_design"]
    request = interface_design.get("request")
    if not isinstance(interface_design.get("restful_style"), dict):
        raise ValueError("接口详细设计缺少 RESTful 设计：interface_design.restful_style")
    if not isinstance(request, dict):
        raise ValueError("接口详细设计缺少请求设计：interface_design.request")
    for parameter_field in (
        "path_parameters",
        "query_parameters",
        "header_parameters",
    ):
        if not isinstance(request.get(parameter_field), list):
            raise ValueError(f"接口详细设计字段类型错误：interface_design.request.{parameter_field} 必须是数组")
    for request_object_field in ("request_body", "file_upload"):
        if not isinstance(request.get(request_object_field), dict):
            raise ValueError(f"接口详细设计缺少请求结构：interface_design.request.{request_object_field}")
    if not isinstance(interface_design.get("response_format"), dict):
        raise ValueError("接口详细设计缺少返回格式：interface_design.response_format")
