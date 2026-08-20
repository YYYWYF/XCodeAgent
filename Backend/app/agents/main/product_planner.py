from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.messages import AIMessageChunk

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.product_plan import create_product_plan, validate_product_plan_model_output
from app.utils.model_output import extract_json_object


def _model_product_plan_view(plan: dict[str, Any]) -> dict[str, Any]:
    """只把当前模型契约允许的产品语义字段提供给修订轮次。"""

    return {
        key: plan.get(key)
        for key in (
            "app",
            "user_roles",
            "business_flows",
            "pages",
            "product_acceptance_criteria",
        )
    }


def _product_plan_json_example(requirement_spec: dict[str, Any]) -> str:
    """按 RequirementSpec 页面身份生成完整且可直接遵循的 JSON 响应结构示例。"""

    role_ids = [
        str(item.get("id") or "").strip()
        for item in requirement_spec.get("user_roles", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    pages: list[dict[str, Any]] = []
    for source in requirement_spec.get("pages", []):
        if not isinstance(source, dict):
            continue
        page_id = str(source.get("pageId") or source.get("id") or "page")
        pages.append(
            {
                "pageId": page_id,
                "name": str(source.get("name") or "页面名称"),
                "path": str(source.get("path") or f"/{page_id}"),
                "module_id": str(source.get("module_id") or "core"),
                "description": str(source.get("description") or "页面说明"),
                "goal": "<填写该页面的产品目标>",
                "information_items": [
                    {
                        "itemId": f"{page_id}-primary-information",
                        "label": "<填写业务信息名称>",
                        "description": "<填写用户在该页面需要理解的业务信息>",
                    }
                ],
                "actions": [
                    {
                        "actionId": f"{page_id}-primary-action",
                        "name": "<填写用户主动操作名称；纯展示页面删除此示例并返回空数组>",
                        "description": "<填写操作意图>",
                        "requiresConfirmation": False,
                        "behavior": {
                            "type": "business",
                            "expectedResult": "<填写可见或业务结果>",
                        },
                    }
                ],
                "navigation_targets": [],
                "allowed_roles": role_ids,
                "state_requirements": {
                    "loading": "<填写加载状态要求>",
                    "empty": "<填写空状态要求>",
                    "error": "<填写错误状态要求>",
                    "success": "<填写成功状态要求>",
                    "validation": "<填写校验状态要求>",
                },
                "acceptance_criteria": ["<填写页面级产品验收标准>"],
            }
        )
    app_info = requirement_spec.get("app_info")
    app_info = app_info if isinstance(app_info, dict) else {}
    example = {
        "app": {
            "name": str(app_info.get("name") or "未命名应用"),
            "summary": str(app_info.get("summary") or requirement_spec.get("summary") or ""),
        },
        "user_roles": requirement_spec.get("user_roles", []),
        "business_flows": requirement_spec.get("business_flows", []),
        "pages": pages,
        "product_acceptance_criteria": ["<填写产品级验收标准>"],
    }
    return json.dumps(example, ensure_ascii=False, indent=2)


def _product_planning_prompt(
    requirement_spec: dict[str, Any],
    existing_plan: dict[str, Any] | None = None,
    user_feedback: str = "",
) -> str:
    """构造只负责产品语义、不包含技术实现的产品规划提示。"""

    revision_context = (
        "Treat the latest product feedback as an incremental patch to the existing ProductPlan, never "
        "as a replacement plan. Start from the complete existing plan, change only facts explicitly "
        "affected by the feedback or by the confirmed RequirementSpec, and preserve every unrelated "
        "page goal, information item, action, navigation target, state requirement, acceptance criterion, "
        "and app summary. Preserve stable pageId, itemId, actionId, and stepId values for unchanged "
        "product concepts. Return the complete merged ProductPlan, not a patch and not a plan describing "
        "only the latest feedback.\n"
        f"Existing ProductPlan:\n{json.dumps(_model_product_plan_view(existing_plan), ensure_ascii=False)}\n\n"
        if existing_plan
        else "Create a new ProductPlan from the confirmed RequirementSpec.\n"
    )
    return (
        "You are the product-planning model for an app-generation workflow.\n"
        "This is a product-only boundary. Do not design APIs, schemas, database tables, technical "
        "architecture, data sources, persistence choices, code files, React components, or visual layout. "
        "Never ask the product reviewer to confirm data-source or storage decisions.\n"
        "Return one complete JSON object without markdown fences. The root object must contain exactly "
        "app, user_roles, business_flows, pages, and product_acceptance_criteria. Never return frontend_pages "
        "or any other root field. Do not return assumptions or product "
        "risks. Product uncertainty must be resolved during requirements clarification, while technical "
        "risk belongs to later technical planning and execution.\n"
        "All page acceptance_criteria and product_acceptance_criteria must describe only observable "
        "product behavior for users of the generated application. Never include XCodeAgent workflow "
        "stages, preview availability, code generation, build/compile/lint/typecheck status, automated "
        "or integration tests, quality gates, or conditions for entering user acceptance.\n"
        "The page set is immutable: pages must match RequirementSpec.pages one-to-one by pageId, name, "
        "path, module_id, and description. For each page add: goal, information_items, actions, "
        "navigation_targets, allowed_roles, state_requirements, and acceptance_criteria.\n"
        "information_items MUST be JSON objects shaped exactly as "
        "{itemId, label, description}; never serialize objects as strings. itemId is stable within its page.\n"
        "An action is an explicit user-triggered intent that changes visible UI state, changes a result set, "
        "navigates, submits or mutates business information, downloads/exports, or opens an external target. "
        "Passive viewing, reading, browsing, scrolling, noticing content, or the mere presence of information "
        "is NOT an action; express it in information_items or acceptance_criteria instead. A display-only page "
        "may therefore have an empty actions list.\n"
        "Every real action must have stable actionId, name, description, requiresConfirmation boolean, and "
        "a product-readable behavior object. behavior.type is one of business, navigation, interface, external, "
        "or sequence; behavior.expectedResult states the visible/business outcome in product language. "
        "navigation also requires targetPageId; external requires externalTarget; sequence requires ordered "
        "steps shaped {stepId,type,expectedResult,targetPageId? or externalTarget?}, where step type cannot be "
        "sequence. Use interface for opening/closing dialogs, switching tabs, expanding regions, or other "
        "purely local presentation changes. Use business for querying, submitting, mutating, exporting, or "
        "other domain outcomes whose implementation is decided later. Actions must not contain endpoint ids, HTTP "
        "details, schemas, data sources, database operations, or implementation guesses.\n"
        "navigation_targets may only reference pageIds declared in the same plan. allowed_roles may only "
        "reference RequirementSpec.user_roles ids. Every navigation behavior targetPageId must also appear "
        "in that page's navigation_targets, including a targetPageId equal to the current pageId. "
        "state_requirements should cover loading, empty, error, "
        "success, and validation.\n"
        "Follow this complete JSON response structure exactly. Replace angle-bracket placeholders with "
        "product facts; do not add keys. For navigation/external/sequence behavior, replace the behavior "
        "object with the exact type-specific fields described above.\n"
        f"Complete JSON response example:\n{_product_plan_json_example(requirement_spec)}\n\n"
        f"{revision_context}"
        f"Latest product feedback:\n{user_feedback}\n\n"
        f"Confirmed RequirementSpec:\n{json.dumps(requirement_spec, ensure_ascii=False)}"
    )


def _invoke_product_planner(
    requirement_spec: dict[str, Any],
    *,
    existing_plan: dict[str, Any] | None = None,
    user_feedback: str = "",
    on_token: Callable[[str], None] | None = None,
) -> str:
    """调用产品规划 ChatModel，并可选透传流式文本。

    GLM-5.2 默认开启深度思考，thinking 与正文共享 max_tokens。ProductPlan JSON
    体量大（多页 information_items/actions/state_requirements），thinking 会挤占
    输出预算，常在写完闭合括号前被截断，流式投到前端的原始 JSON 也随之截断、
    解析校验失败。与 UI 设计稿生成一致，关闭 thinking 释放输出预算。
    """

    model = create_chat_model(
        Settings.from_env(),
        extra_model_kwargs={"thinking": {"type": "disabled"}},
    )
    prompt = _product_planning_prompt(requirement_spec, existing_plan, user_feedback)
    if on_token is None:
        result = model.invoke(prompt)
        return _coerce_content_text(getattr(result, "content", "")) or ""

    accumulated = ""
    for chunk in model.stream(prompt):
        if not isinstance(chunk, AIMessageChunk):
            continue
        token = _coerce_content_text(chunk.content)
        if token:
            accumulated += token
            on_token(token)
    return accumulated


def plan_product_with_chat_model(
    requirement_spec: dict[str, Any],
    *,
    existing_plan: dict[str, Any] | None = None,
    user_feedback: str = "",
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """生成经过确定性归一化的 ProductPlan。"""

    agent_note = _invoke_product_planner(
        requirement_spec,
        existing_plan=existing_plan,
        user_feedback=user_feedback,
        on_token=on_token,
    )
    agent_plan = extract_json_object(agent_note)
    format_errors = validate_product_plan_model_output(agent_plan, requirement_spec)
    if format_errors:
        raise ValueError("ProductPlan 模型 JSON 格式校验失败：" + "；".join(format_errors))
    return create_product_plan(
        requirement_spec,
        agent_plan=agent_plan,
        existing_plan=existing_plan,
    )
