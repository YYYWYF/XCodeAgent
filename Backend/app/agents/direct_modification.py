from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.model_factory import create_chat_model
from app.agents.tool_activity_stream import (
    ToolActivityCallback,
    invoke_agent_with_tool_activity,
)
from app.config import Settings
from app.middleware.direct_modification import DIRECT_MODIFICATION_MODE_MARKER
from app.services.direct_modification import parse_direct_modification_agent_result
from app.utils.model_output import extract_json_object
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


DirectModificationOwner = Literal["frontend", "backend", "fullstack", "unknown"]
DirectModificationScope = Literal["direct", "requires_planning", "needs_clarification"]

_MAX_CONVERSATION_SUMMARY_CHARS = 4_000
_FRONTEND_REQUIRED_SKILLS = (
    "/.xcodeagent/builtin-skills/code-block-template/SKILL.md",
    "/.xcodeagent/builtin-skills/react-develop-specification/SKILL.md",
)


@dataclass(frozen=True)
class DirectModificationDecision:
    """保存快速修改意图分类的规范化结果。"""

    owner: DirectModificationOwner
    scope: DirectModificationScope
    confidence: float
    reason: str
    clarification_question: str


def _direct_modification_classifier_prompt(
    *,
    user_request: str,
    conversation_summary: str,
) -> str:
    """构造只负责执行归属和范围判断的快速修改分类 Prompt。"""

    return (
        "Classify a coding modification request by semantic intent. Return exactly one JSON "
        "object and no Markdown. Do not route by isolated keywords such as API, button, or page.\n\n"
        "Owners:\n"
        "- frontend: only UI, React, styling, browser interaction, or frontend API-consumption code.\n"
        "- backend: only server, data model, persistence, validation, or API implementation code.\n"
        "- fullstack: a localized request that requires both backend and frontend changes. A request "
        "must not be rejected merely because it is fullstack.\n"
        "- unknown: there is not enough information to choose safely.\n\n"
        "Scopes:\n"
        "- direct: a localized change that can be implemented directly, including localized fullstack work.\n"
        "- needs_clarification: the requested behavior or modification location is too ambiguous.\n"
        "- requires_planning: only broad architecture replacement, large data migration, new application, "
        "or a change whose product decisions cannot be made safely as a localized edit.\n\n"
        "Return this shape:\n"
        '{"owner":"frontend|backend|fullstack|unknown",'
        '"scope":"direct|requires_planning|needs_clarification",'
        '"confidence":0.0,"reason":"short reason",'
        '"clarificationQuestion":"question when clarification is needed"}\n\n'
        f"Bounded quick-chat summary:\n{conversation_summary or '(empty)'}\n\n"
        f"Current user request:\n{user_request}"
    )


def classify_direct_modification_intent(
    *,
    user_request: str,
    conversation_summary: str = "",
) -> DirectModificationDecision:
    """调用配置模型判断快速修改的执行 Agent 和可直接处理范围。"""

    if not user_request.strip():
        return _clarification_fallback("用户没有提供可执行的修改需求。")
    try:
        model = create_chat_model(Settings.from_env())
        response = model.invoke(
            [
                SystemMessage(
                    content="You are a conservative coding-request router. Output JSON only."
                ),
                HumanMessage(
                    content=_direct_modification_classifier_prompt(
                        user_request=user_request.strip(),
                        conversation_summary=conversation_summary[
                            -_MAX_CONVERSATION_SUMMARY_CHARS:
                        ],
                    )
                ),
            ]
        )
        content = getattr(response, "content", "")
        text = content if isinstance(content, str) else str(content or "")
        return _normalize_direct_modification_decision(extract_json_object(text) or {})
    except Exception as exc:
        return _clarification_fallback(
            f"意图识别失败（{type(exc).__name__}），需要用户补充后重试。"
        )


def _normalize_direct_modification_decision(
    payload: dict[str, Any],
) -> DirectModificationDecision:
    """校验模型分类结果，并对低置信度结果执行安全降级。"""

    owner = str(payload.get("owner") or "unknown")
    scope = str(payload.get("scope") or "needs_clarification")
    if owner not in {"frontend", "backend", "fullstack", "unknown"}:
        owner = "unknown"
    if scope not in {"direct", "requires_planning", "needs_clarification"}:
        scope = "needs_clarification"
    try:
        confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(payload.get("reason") or "模型没有提供有效的分类依据。").strip()
    clarification_question = str(
        payload.get("clarificationQuestion")
        or payload.get("clarification_question")
        or "请补充要修改的功能、页面、组件或接口，以及期望结果。"
    ).strip()
    if confidence < 0.65 or owner == "unknown":
        return DirectModificationDecision(
            owner="unknown",
            scope="needs_clarification",
            confidence=confidence,
            reason=reason,
            clarification_question=clarification_question,
        )
    return DirectModificationDecision(
        owner=owner,  # type: ignore[arg-type]
        scope=scope,  # type: ignore[arg-type]
        confidence=confidence,
        reason=reason,
        clarification_question=clarification_question,
    )


def _clarification_fallback(reason: str) -> DirectModificationDecision:
    """在分类不可用时返回不修改代码的澄清结果。"""

    return DirectModificationDecision(
        owner="unknown",
        scope="needs_clarification",
        confidence=0.0,
        reason=reason,
        clarification_question="请补充要修改的功能、页面、组件或接口，以及期望结果。",
    )


def _frontend_direct_modification_prompt(
    *,
    user_request: str,
    conversation_summary: str,
    backend_handoff: dict[str, Any] | None = None,
) -> str:
    """构造不依赖正式计划产物的前端快速修改 Prompt。"""

    handoff = json.dumps(backend_handoff or {}, ensure_ascii=False, indent=2)[:8_000]
    return (
        f"{DIRECT_MODIFICATION_MODE_MARKER}\n"
        "You are handling a localized frontend modification in an existing application.\n"
        "Do not create, read as an execution contract, or depend on RequirementSpec, ProjectPlan, "
        "BuildTaskPlan, approved tasks, or a task DAG. Inspect the existing implementation, make "
        "the smallest safe change, and verify it with the repository's existing frontend commands.\n"
        "Write only frontend application code. You may read backend code and the backend handoff, "
        "but must not modify backend files. Never create temporary scripts for verification.\n"
        "This is a direct-edit run: task and write_todos are unavailable. Do not delegate discovery "
        "or verification. Inspect relevant code progressively and avoid unrelated repository-wide "
        "scans. After editing, call execute yourself for the focused existing typechecks, tests, or "
        "build commands appropriate to the actual change scope. Keep verification proportional for "
        "style-only changes. If command execution is unavailable or fails, report that verification "
        "result; never replace command execution with a manual repository-wide review.\n"
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}\n\n"
        "## Mandatory built-in skills\n"
        "Before writing any code, use read_file(limit=400) to read each file below completely and "
        "follow it. If either read fails, do not modify code and return a failed result.\n"
        f"1. `{_FRONTEND_REQUIRED_SKILLS[0]}`\n"
        f"2. `{_FRONTEND_REQUIRED_SKILLS[1]}`\n"
        "Read code-block-template references only when relevant to this request; do not load all "
        "references up front. These built-in skills are mounted read-only and are independent of "
        "the user-selected skill list.\n\n"
        "Return exactly one JSON object with: status (completed or failed), summary, changedFiles, "
        "verification, alreadySatisfied, and failureReason. Set alreadySatisfied=true only after "
        "verifying that the exact requested behavior already exists. Do not return Markdown around "
        "the JSON object.\n\n"
        f"Bounded quick-chat summary:\n{conversation_summary or '(empty)'}\n\n"
        f"Backend handoff for this run:\n{handoff}\n\n"
        f"Current user request:\n{user_request}"
    )


def _data_source_direct_modification_prompt(
    *,
    user_request: str,
    conversation_summary: str,
) -> str:
    """构造暂不要求内置 Skill 的后端快速修改 Prompt。"""

    return (
        f"{DIRECT_MODIFICATION_MODE_MARKER}\n"
        "You are handling a localized backend/data-source modification in an existing application.\n"
        "Do not create, read as an execution contract, or depend on RequirementSpec, ProjectPlan, "
        "BuildTaskPlan, approved tasks, or a task DAG. Inspect the existing implementation, make "
        "the smallest safe change, and verify it with the repository's existing backend commands.\n"
        "Write only backend application code. You may read frontend code to understand consumers, "
        "but must not modify frontend files. There are currently no mandatory built-in skills for "
        "this direct backend flow. Never create temporary scripts for verification.\n"
        "This is a direct-edit run: task and write_todos are unavailable. Do not delegate discovery "
        "or verification. Inspect relevant files progressively and avoid unrelated repository-wide "
        "scans. After editing, call execute yourself for the focused existing backend tests, checks, "
        "or build commands appropriate to the actual change scope. If command execution is unavailable "
        "or fails, report that result; never replace it with a manual repository-wide review.\n"
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}\n\n"
        "Return exactly one JSON object with: status (completed or failed), summary, changedFiles, "
        "verification, alreadySatisfied, failureReason, and backendHandoff. backendHandoff must "
        "contain summary, endpoints, changedFiles, and notes; endpoints must describe method, path, "
        "request, and response when an API changed. Set alreadySatisfied=true only after verifying "
        "the exact requested behavior already exists. Do not return Markdown around the JSON object.\n\n"
        f"Bounded quick-chat summary:\n{conversation_summary or '(empty)'}\n\n"
        f"Current user request:\n{user_request}"
    )


def invoke_frontend_direct_modification(
    *,
    user_request: str,
    conversation_summary: str,
    backend_handoff: dict[str, Any] | None,
    workspace: str | None,
    selected_skill_names: list[str] | None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> str:
    """复用 Frontend Agent 执行快速修改专用 Prompt。"""

    from app.agents import create_agent_bundle

    return invoke_agent_with_tool_activity(
        create_agent_bundle(workspace, selected_skill_names).frontend,
        {
            "messages": [
                {
                    "role": "user",
                    "content": _frontend_direct_modification_prompt(
                        user_request=user_request,
                        conversation_summary=conversation_summary,
                        backend_handoff=backend_handoff,
                    ),
                }
            ]
        },
        workspace=workspace,
        on_tool_activity=on_tool_activity,
    )


def invoke_data_source_direct_modification(
    *,
    user_request: str,
    conversation_summary: str,
    workspace: str | None,
    selected_skill_names: list[str] | None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> str:
    """复用 Data Source Agent 执行快速修改专用 Prompt。"""

    from app.agents import create_agent_bundle

    return invoke_agent_with_tool_activity(
        create_agent_bundle(workspace, selected_skill_names).data_source,
        {
            "messages": [
                {
                    "role": "user",
                    "content": _data_source_direct_modification_prompt(
                        user_request=user_request,
                        conversation_summary=conversation_summary,
                    ),
                }
            ]
        },
        workspace=workspace,
        on_tool_activity=on_tool_activity,
    )
