from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.data_source_policy import DatasourceType
from app.services.requirement_spec import create_requirement_spec
from app.tools.ask_user import ask_user, extract_ask_user_clarification
from app.utils.model_output import extract_json_object


def _requirements_prompt(
    request: str,
    existing_spec: dict[str, Any] | None = None,
    datasource_type: DatasourceType = "database",
) -> str:
    """构建产品需求提示；保留实体清单并把技术配置下沉到后续阶段。"""

    visible_existing_spec = (
        {
            key: value
            for key, value in existing_spec.items()
            if key not in {"data_sources", "acceptance_criteria"}
        }
        if isinstance(existing_spec, dict)
        else None
    )
    revision_context = (
        "Use the existing RequirementSpec and the latest user feedback together as inputs. Generate "
        "one new, complete RequirementSpec whose full contents authoritatively replace the old document; "
        "never return a partial patch. Treat the latest feedback as an incremental patch to the product, "
        "not as a replacement application request. Start from every existing product fact, apply "
        "only the additions, removals, or replacements explicitly requested by the feedback, and keep "
        "all unrelated fields semantically unchanged. The latest feedback overrides only directly "
        "conflicting older requirements. Preserve stable ids for unchanged items, remove items the user "
        "no longer wants, and add newly requested items. app_info.summary must summarize the complete "
        "merged application after the patch; it must not contain only the latest feedback.\n"
        f"Existing RequirementSpec:\n{json.dumps(visible_existing_spec, ensure_ascii=False)}\n\n"
        if visible_existing_spec
        else "Create a new RequirementSpec from the user request.\n"
    )
    clarification_policy = (
        "Before asking the user, silently audit every required aspect together, including the "
        "product goals, page inventory, user roles, business flows, and information needs. "
        "In each clarification turn, batch every material missing "
        "or ambiguous item into one to four focused questions. An application name and a broad scenario "
        "alone are not sufficient when roles, core tasks, page boundaries, permissions, or "
        "the primary business flow cannot be inferred safely. Ask only about gaps that would materially "
        "change the product design; omit optional details that the user did not request instead of "
        "inventing assumptions. Do not "
        "ask open-ended follow-up questions such as whether there are more roles, pages, "
        "or optional features after the user has answered a prior clarification turn.\n"
    )
    followup_policy = (
        "The latest request contains answers to a previous clarification turn. Treat these answers as "
        "the user's confirmation for the asked dimensions. Merge them into a complete RequirementSpec "
        "now. Do not call ask_user again for the same dimensions, and do not ask optional 'any other "
        "roles/pages/features' questions. Omit optional details that remain unspecified.\n"
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
        "页面清单, 实体清单, 业务信息需求, 业务流程.\n"
        "This stage only generates business entities. Each entity has a stable id, name, description, "
        "and fields that are display-only business information (label and description only). Do NOT "
        "generate field names, field types, data sources, or any database/external-api/static choice "
        "here or in product/technical planning; the data source for each entity is decided and "
        "confirmed in the entity-design stage. Never emit data_sources or the legacy type mock.\n"
        "Databases, persistence, API contracts, schemas, and storage choices are technical "
        "planning concerns. Do not ask the product user about them, do not expose them for confirmation, "
        "and do not include data_sources in the returned RequirementSpec.\n"
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
        "feature_modules, pages, entities, and business_flows. Do not return assumptions, product risks, "
        "or acceptance_criteria. "
        "Product acceptance criteria belong to the later ProductPlan and must not be generated in the "
        "RequirementSpec confirmation document. "
        "Every role, module, entity, and flow must have a stable id. Every page must have a "
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
    datasource_type: DatasourceType = "database",
    settings: Settings | None = None,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """调用绑定澄清工具的需求模型，可选流式吐词。"""

    active_settings = settings or Settings.from_env()
    runnable = create_chat_model(active_settings).bind_tools([ask_user])
    if on_token is None:
        result = runnable.invoke(_requirements_prompt(request, existing_spec, datasource_type))
        return {"messages": [result]}

    accumulated_text = ""
    merged_chunk: AIMessageChunk | None = None
    for chunk in runnable.stream(_requirements_prompt(request, existing_spec, datasource_type)):
        if isinstance(chunk, AIMessageChunk):
            # glm-5.2 流式 chunk.content 是 content block 列表（如
            # [{"text": "...", "type": "text", "index": 0}]），不是纯字符串。
            # 用 _coerce_content_text 提取 text block，否则 accumulated_text 永远为空，
            # 导致最终 agent_note 为空、spec 解析失败、应用名与页面退回固定 fallback。
            token = _coerce_content_text(chunk.content)
            if token:
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
    datasource_type: DatasourceType = "database",
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """直接调用需求模型生成 RequirementSpec，并在关键需求不足时请求澄清。"""

    settings = Settings.from_env()
    agent_result = _invoke_live_chat_model(
        request,
        existing_spec=existing_spec,
        datasource_type=datasource_type,
        settings=settings,
        on_token=on_token,
    )
    messages = agent_result.get("messages", [])
    content = getattr(messages[-1], "content", "") if messages else ""
    agent_note = _coerce_content_text(content) or ""
    analysis_source = "direct_chat_model"

    agent_spec = extract_json_object(agent_note)
    asks_for_clarification = any(
        getattr(message, "tool_calls", None) for message in messages
    )
    if isinstance(existing_spec, dict) and not asks_for_clarification:
        _validate_complete_revised_requirement_spec(agent_spec)
    spec = create_requirement_spec(
        request,
        agent_note=agent_note,
        agent_spec=agent_spec,
        existing_spec=existing_spec,
        authoritative_agent_spec=(
            isinstance(existing_spec, dict) and isinstance(agent_spec, dict)
        ),
        datasource_type=datasource_type,
    )
    clarification = extract_ask_user_clarification(agent_result, spec)
    spec["clarification_questions"] = clarification["questions"]
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


def _validate_complete_revised_requirement_spec(agent_spec: Any) -> None:
    """修订时拒绝不完整模型结果，避免缺失字段静默回退成旧需求文档。"""

    if not isinstance(agent_spec, dict):
        raise ValueError("需求 AI 未返回完整的新 RequirementSpec。")
    app_info = agent_spec.get("app_info")
    missing_fields = [
        field_name
        for field_name in (
            "user_roles",
            "feature_modules",
            "pages",
            "business_flows",
        )
        if not isinstance(agent_spec.get(field_name), list)
    ]
    if not isinstance(app_info, dict):
        missing_fields.insert(0, "app_info")
    elif not str(app_info.get("name") or "").strip() or not str(
        app_info.get("summary") or ""
    ).strip():
        missing_fields.insert(0, "app_info.name/summary")
    if missing_fields:
        raise ValueError(
            "需求 AI 返回的新 RequirementSpec 缺少完整字段："
            + "、".join(missing_fields)
        )
