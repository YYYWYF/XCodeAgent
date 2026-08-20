from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field


QuestionType = Literal["choice", "text", "yesno"]
OTHER_OPTION_VALUE = "__other__"
MAX_ASK_USER_QUESTIONS = 8


class AskUserOption(BaseModel):
    label: str = Field(description="Display text, ideally 1-5 words.")
    description: str = Field(description="Brief explanation of this option.")
    value: str | None = Field(
        default=None,
        description="Stable submitted value. Omit for ordinary options.",
    )


class AskUserQuestion(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    question: str = Field(description="The complete question text.")
    header: str = Field(description="A short label displayed as a chip/tag.")
    type: QuestionType = Field(default="choice", description="Question input type.")
    options: list[AskUserOption] | None = Field(
        default=None,
        description="Required for choice questions. Provide 2-4 business options.",
    )
    multi_select: bool = Field(
        default=False,
        alias="multiSelect",
        description=(
            "Set true when options are independently combinable capabilities or requirements, "
            "such as search, filter, import/export, and pagination. Keep false only when the "
            "options are mutually exclusive alternatives."
        ),
    )
    placeholder: str | None = Field(
        default=None,
        description="Hint text for free-form text input.",
    )
    allow_other: bool = Field(
        default=True,
        alias="allowOther",
        description="Whether a selectable question includes an Other option with free-form input.",
    )


class AskUserInput(BaseModel):
    questions: list[AskUserQuestion] = Field(
        min_length=1,
        max_length=MAX_ASK_USER_QUESTIONS,
        description=f"One to {MAX_ASK_USER_QUESTIONS} questions to ask the user.",
    )


def _normalize_question(question: AskUserQuestion) -> dict[str, Any]:
    payload = question.model_dump(by_alias=True, exclude_none=True)
    payload["header"] = str(payload["header"])[:16]
    if payload.get("type") == "choice":
        options = payload.get("options")
        if not isinstance(options, list) or len(options) < 2:
            raise ValueError("choice questions require 2-4 options")
        payload["options"] = _with_other_option(options)
    elif payload.get("type") == "yesno":
        payload.pop("options", None)
        payload["allowOther"] = True
    else:
        payload.pop("options", None)
        payload.pop("multiSelect", None)
        payload.pop("allowOther", None)
    return payload


def _with_other_option(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    business_options = [
        option
        for option in options
        if option.get("value") != OTHER_OPTION_VALUE
        and option.get("label") != "其他"
    ][:4]
    return [
        *business_options,
        {
            "label": "其他",
            "description": "补充未覆盖的需求或偏好。",
            "value": OTHER_OPTION_VALUE,
        },
    ]


def build_ask_user_payload(questions: list[AskUserQuestion]) -> dict[str, Any]:
    normalized_questions = [_normalize_question(question) for question in questions]
    return {
        "mode": "ask_user_question",
        "status": "requires_user_input",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": normalized_questions,
        "assumptions": [],
        "message": "Agent requested user input before continuing.",
    }


@tool("ask_user", args_schema=AskUserInput)
def ask_user(questions: list[AskUserQuestion]) -> str:
    """Ask the user one to eight questions before continuing the workflow."""

    return json.dumps(build_ask_user_payload(questions), ensure_ascii=False)


def clear_clarification(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "ask_user_question",
        "status": "clear",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": [],
        "assumptions": [],
        "message": "需求关键维度已覆盖，暂不需要追问用户。",
        "spec_summary": spec.get("app_info", {}).get("name", "未命名应用"),
    }


def extract_ask_user_clarification(
    agent_result: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Extract the latest ask_user request emitted by an agent run."""

    messages = agent_result.get("messages", [])
    for message in reversed(messages):
        payload = _payload_from_tool_message(message)
        if payload:
            payload["spec_summary"] = spec.get("app_info", {}).get("name", "未命名应用")
            return payload

        payload = _payload_from_ai_tool_call(message)
        if payload:
            payload["spec_summary"] = spec.get("app_info", {}).get("name", "未命名应用")
            return payload

    return clear_clarification(spec)


def _payload_from_tool_message(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, ToolMessage):
        return None
    if getattr(message, "name", None) not in {None, "ask_user"}:
        return None
    payload = _json_object(getattr(message, "content", ""))
    return _valid_ask_user_payload(payload)


def _payload_from_ai_tool_call(message: Any) -> dict[str, Any] | None:
    tool_calls = getattr(message, "tool_calls", None)
    if not isinstance(tool_calls, list):
        return None
    for tool_call in reversed(tool_calls):
        if not isinstance(tool_call, dict) or tool_call.get("name") != "ask_user":
            continue
        args = tool_call.get("args") or tool_call.get("input") or {}
        if not isinstance(args, dict):
            continue
        raw_questions = args.get("questions")
        if not isinstance(raw_questions, list):
            continue
        try:
            questions = [AskUserQuestion.model_validate(item) for item in raw_questions]
            return build_ask_user_payload(questions)
        except (TypeError, ValueError):
            return None
    return None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _valid_ask_user_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("question_schema") != "gemini_cli.ask_user.v1":
        return None
    questions = payload.get("questions")
    if not isinstance(questions, list):
        return None
    try:
        normalized = build_ask_user_payload(
            [
                AskUserQuestion.model_validate(question)
                for question in questions[:MAX_ASK_USER_QUESTIONS]
            ]
        )
    except (TypeError, ValueError):
        return None
    return {
        **normalized,
        "mode": payload.get("mode") or normalized["mode"],
        "status": payload.get("status") or normalized["status"],
        "assumptions": payload.get("assumptions")
        if isinstance(payload.get("assumptions"), list)
        else [],
        "message": payload.get("message") or normalized["message"],
    }
