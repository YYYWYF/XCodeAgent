from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.data_source_policy import DatasourceType
from app.services.requirement_spec import (
    create_requirement_spec,
    merge_clarification_answers_into_spec,
)
from app.tools.ask_user import ask_user, extract_ask_user_clarification
from app.utils.model_output import extract_json_object


MAX_REQUIREMENT_CLARIFICATION_ROUNDS = 3
MIN_REQUIREMENT_CLARIFICATION_QUESTIONS = 5
MAX_REQUIREMENT_CLARIFICATION_QUESTIONS = 8


def _requirements_prompt(
    request: str,
    existing_spec: dict[str, Any] | None = None,
    datasource_type: DatasourceType = "database",
    clarification_round: int = 0,
) -> str:
    """构建产品需求提示；保留实体清单并把技术配置下沉到后续阶段。"""

    bounded_round = max(0, min(clarification_round, MAX_REQUIREMENT_CLARIFICATION_ROUNDS))
    next_round = min(
        bounded_round + 1,
        MAX_REQUIREMENT_CLARIFICATION_ROUNDS,
    )

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
    round_context = (
        f"The user has already completed all {MAX_REQUIREMENT_CLARIFICATION_ROUNDS} clarification rounds. "
        f"This is the final consolidation pass after round {MAX_REQUIREMENT_CLARIFICATION_ROUNDS}; "
        "never call ask_user in this pass. Merge the latest answers into the best complete JSON and "
        "leave any still-optional detail empty.\n"
        if bounded_round >= MAX_REQUIREMENT_CLARIFICATION_ROUNDS
        else (
            f"This request is entering clarification round {next_round} of "
            f"{MAX_REQUIREMENT_CLARIFICATION_ROUNDS}; the number counts rounds already completed "
            f"before this model call ({bounded_round}) plus this call. At the final round, ask the "
            "last batch once if material gaps remain; do not defer questions to a later round.\n"
        )
    )
    clarification_policy = (
        "Before asking the user, silently audit every required aspect together, including the "
        "product goals, page inventory, user roles, business flows, and information needs. "
        f"This workflow allows at most {MAX_REQUIREMENT_CLARIFICATION_ROUNDS} clarification rounds. "
        f"{round_context}"
        f"In each clarification turn, batch every material missing or ambiguous item into "
        f"{MIN_REQUIREMENT_CLARIFICATION_QUESTIONS} to {MAX_REQUIREMENT_CLARIFICATION_QUESTIONS} focused questions "
        "when at least that many material gaps remain. If fewer than "
        f"{MIN_REQUIREMENT_CLARIFICATION_QUESTIONS} material gaps remain, ask exactly all remaining gaps and do not "
        "invent filler questions. An application name and a broad scenario "
        "alone are not sufficient when roles, core tasks, page boundaries, permissions, or "
        "the primary business flow cannot be inferred safely. Ask only about gaps that would materially "
        "change the product design; omit optional details that the user did not request instead of "
        "inventing assumptions. Do not "
        "ask open-ended follow-up questions such as whether there are more roles, pages, "
        "or optional features after the user has answered a prior clarification turn. "
        "Never use ask_user to ask whether the requirements are complete, whether the user has "
        "anything else to add, or to request generic confirmation; when no material gap remains, "
        "return the complete JSON and let the workflow's artifact confirmation UI handle approval. "
        f"After the user has answered round {MAX_REQUIREMENT_CLARIFICATION_ROUNDS}, never call ask_user again "
        "under any circumstance; return the best complete JSON supported by the request and answers, "
        "leaving genuinely unspecified optional details empty for the confirmation UI.\n"
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
    clarification_round: int = 0,
    settings: Settings | None = None,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """调用绑定澄清工具的需求模型，可选流式吐词。"""

    active_settings = settings or Settings.from_env()
    runnable = create_chat_model(active_settings).bind_tools([ask_user])
    if on_token is None:
        result = runnable.invoke(
            _requirements_prompt(
                request,
                existing_spec,
                datasource_type,
                clarification_round,
            )
        )
        return {"messages": [result]}

    accumulated_text = ""
    merged_chunk: AIMessageChunk | None = None
    for chunk in runnable.stream(
        _requirements_prompt(
            request,
            existing_spec,
            datasource_type,
            clarification_round,
        )
    ):
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
    clarification_round: int = 0,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """直接调用需求模型生成 RequirementSpec，并在关键需求不足时请求澄清。"""

    settings = Settings.from_env()
    agent_result = _invoke_live_chat_model(
        request,
        existing_spec=existing_spec,
        datasource_type=datasource_type,
        clarification_round=clarification_round,
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
    if not asks_for_clarification and not isinstance(agent_spec, dict):
        # 需求已被判定为清晰时必须有完整 JSON，不能退回固定页面模板继续向下游传播。
        raise ValueError("需求 AI 未返回完整 RequirementSpec JSON。")
    # 调用 ask_user 时只展示问题；即使消息同时夹带 JSON，也必须等下一轮完整回答后再采用。
    effective_agent_spec = agent_spec if not asks_for_clarification else None
    if isinstance(existing_spec, dict) and not asks_for_clarification:
        _validate_complete_revised_requirement_spec(effective_agent_spec)
    spec = create_requirement_spec(
        request,
        agent_note=agent_note,
        agent_spec=effective_agent_spec,
        existing_spec=existing_spec,
        authoritative_agent_spec=(
            isinstance(existing_spec, dict) and isinstance(effective_agent_spec, dict)
        ),
        datasource_type=datasource_type,
        # 实时模型的 RequirementSpec 必须完全来自模型或用户明确回答，禁止启用固定页面兜底。
        allow_inferred_defaults=False,
    )
    if _is_clarification_followup(existing_spec):
        # 澄清答案先做确定性合并，避免模型再次遗漏已回答的角色、页面或功能事实。
        spec = merge_clarification_answers_into_spec(spec, request)
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


def _is_clarification_followup(existing_spec: dict[str, Any] | None) -> bool:
    """判断当前模型调用是否正在处理上一轮澄清答案。"""

    return bool(
        isinstance(existing_spec, dict)
        and existing_spec.get("confirmation_status") == "pending_user_input"
    )


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
