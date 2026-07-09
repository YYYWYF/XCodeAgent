from __future__ import annotations

from pathlib import Path
from typing import Any

from app.workspace.plan_documents import load_project_plan_json
from app.workspace.spec_documents import load_requirement_spec_json
from app.workspace.task_documents import load_build_task_plan_json


def workflow_run_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    forwarded_props = _optional_dict(payload.get("forwardedProps")) or {}
    application = _optional_dict(forwarded_props.get("application")) or {}
    state = _optional_dict(payload.get("state")) or {}
    request = (
        _optional_text(payload.get("request"))
        or _optional_text(payload.get("message"))
        or _last_user_message(payload.get("messages"))
    )
    request = _merge_clarification_answers(
        request=request,
        original_request=(
            _optional_text(payload.get("originalRequest"))
            or _optional_text(forwarded_props.get("originalRequest"))
        ),
        clarification_answers=(
            payload.get("clarificationAnswers")
            or forwarded_props.get("clarificationAnswers")
        ),
    )
    resume_state = (
        _optional_dict(payload.get("resumeState"))
        or _optional_dict(payload.get("resume_state"))
        or _optional_dict(forwarded_props.get("resumeState"))
        or _optional_dict(forwarded_props.get("resume_state"))
    )
    debug_state = (
        _optional_dict(payload.get("workflowDebug"))
        or _optional_dict(payload.get("debugState"))
        or _optional_dict(forwarded_props.get("workflowDebug"))
        or _optional_dict(forwarded_props.get("debugState"))
        or {}
    )
    resume_from = (
        _resume_from_state(resume_state)
        or _optional_text(debug_state.get("resume_from"))
        or _optional_text(debug_state.get("resumeFrom"))
        or _optional_text(payload.get("resume_from"))
        or _optional_text(payload.get("resumeFrom"))
        or _optional_text(forwarded_props.get("resume_from"))
        or _optional_text(forwarded_props.get("resumeFrom"))
    )
    if not resume_from and _clarification_answers_to_text(
        payload.get("clarificationAnswers")
        or forwarded_props.get("clarificationAnswers")
    ):
        resume_from = "requirements"
    if not request and resume_from:
        request = f"从 {resume_from} 节点继续执行 workflow 调试。"

    return {
        "request": request,
        "resume_from": resume_from,
        "resume_values": {
            **_resume_values(resume_state),
            **_debug_resume_values(debug_state),
        },
        "project_id": (
            _optional_text(payload.get("project_id"))
            or _optional_text(payload.get("projectId"))
            or _optional_text(state.get("project_id"))
            or _optional_text(state.get("projectId"))
            or _optional_text(application.get("id"))
        ),
        "workspace": (
            _optional_text(payload.get("workspace"))
            or _optional_text(payload.get("workspaceRoot"))
            or _optional_text(forwarded_props.get("workspaceRoot"))
            or _optional_text(application.get("workspaceRoot"))
        ),
        "thread_id": (
            _optional_text(payload.get("thread_id"))
            or _optional_text(payload.get("threadId"))
        ),
        "run_id": (
            _optional_text(payload.get("run_id"))
            or _optional_text(payload.get("runId"))
        ),
    }


def _last_user_message(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""

    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            text = _message_content_to_text(message.get("content"))
            if text:
                return text

    return ""


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif isinstance(item.get("text"), str):
                    parts.append(item["text"])
            elif hasattr(item, "text") and isinstance(item.text, str):
                parts.append(item.text)
        return "\n".join(part for part in parts if part).strip()

    return str(content).strip() if content is not None else ""


def _optional_text(value: Any) -> str:
    return str(value).strip() if value is not None and str(value).strip() else ""


def _optional_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _resume_from_state(value: dict[str, Any] | None) -> str:
    if not value:
        return ""

    events = value.get("events")
    if isinstance(events, list):
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            if event.get("status") != "requires_user_input":
                continue
            node_name = _optional_text(event.get("nodeName"))
            if node_name:
                return _supported_resume_node(node_name)
            node = _optional_dict(event.get("node"))
            node_id = _optional_text(node.get("id")) if node else ""
            if node_id:
                return _supported_resume_node(node_id)

    state = _optional_dict(value.get("state")) or {}
    summary = _optional_dict(value.get("summary")) or {}
    for source in (state, summary, value):
        if source.get("status") == "requires_user_input":
            phase = _optional_text(source.get("phase"))
            if phase:
                return _supported_resume_node(phase)

    return ""


def _supported_resume_node(node_name: str) -> str:
    return (
        node_name
        if node_name
        in {
            "requirements",
            "project_planning",
            "detail_confirmation",
            "prepare_build_tasks",
            "build",
            "integration_test",
            "launch_project",
            "acceptance",
            "finalize_project",
        }
        else ""
    )


def _resume_values(value: dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}

    state = _optional_dict(value.get("state")) or {}
    result = _optional_dict(value.get("result")) or {}
    merged = {**state, **result}
    allowed_keys = {
        "project_plan",
        "pending_project_plan",
        "project_plan_path",
        "project_plan_json_path",
        "detail_selection",
        "selected_page_id",
        "selected_data_source_id",
        "page_spec_draft",
        "confirmed_page_spec",
        "detail_plans",
        "requirement_spec",
        "requirement_spec_path",
        "requirement_spec_json_path",
        "build_task_plan",
        "build_task_plan_path",
        "tasks",
    }
    return {
        key: merged[key]
        for key in allowed_keys
        if key in merged and merged[key] is not None
    }


def _debug_resume_values(debug_state: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    requirement_path = _resolve_debug_json_path(
        debug_state,
        ("requirement_spec_path", "requirementSpecPath", "requirementSpecDirectory"),
        ("requirement-spec.json", "specs/requirement-spec.json"),
    )
    if requirement_path:
        values["requirement_spec_path"] = _markdown_sibling_path(requirement_path)
        values["requirement_spec_json_path"] = str(requirement_path)
        values["requirement_spec"] = load_requirement_spec_json(requirement_path)

    project_plan_path = _resolve_debug_json_path(
        debug_state,
        ("project_plan_path", "projectPlanPath", "projectPlanDirectory"),
        ("project-plan.json", "plans/project-plan.json"),
    )
    if project_plan_path:
        values["project_plan_path"] = _markdown_sibling_path(project_plan_path)
        values["project_plan_json_path"] = str(project_plan_path)
        values["project_plan"] = load_project_plan_json(project_plan_path)

    build_task_plan_path = _resolve_debug_json_path(
        debug_state,
        ("build_task_plan_path", "buildTaskPlanPath", "buildTaskPlanDirectory"),
        ("build-task-plan.json", "plans/build-task-plan.json"),
    )
    if build_task_plan_path:
        build_task_plan = load_build_task_plan_json(build_task_plan_path)
        values["build_task_plan_path"] = str(build_task_plan_path)
        values["build_task_plan"] = build_task_plan
        if isinstance(build_task_plan.get("tasks"), list):
            values["tasks"] = build_task_plan["tasks"]

    return values


def _resolve_debug_json_path(
    debug_state: dict[str, Any],
    field_names: tuple[str, ...],
    default_files: tuple[str, ...],
) -> Path | None:
    raw_path = ""
    for field_name in field_names:
        raw_path = _optional_text(debug_state.get(field_name))
        if raw_path:
            break
    if not raw_path:
        return None

    path = Path(raw_path).expanduser()
    if path.is_file():
        if path.suffix == ".json":
            return path
        json_sibling = path.with_suffix(".json")
        return json_sibling if json_sibling.exists() else None

    candidates = [path / default_file for default_file in default_files]
    return next((candidate for candidate in candidates if candidate.exists()), None)


def _markdown_sibling_path(json_path: Path) -> str:
    markdown_path = json_path.with_suffix(".md")
    return str(markdown_path)


def _merge_clarification_answers(
    *,
    request: str,
    original_request: str,
    clarification_answers: Any,
) -> str:
    answers_text = _clarification_answers_to_text(clarification_answers)
    if not answers_text:
        return request

    base_request = original_request or request
    return "\n".join(
        [
            "请基于原始需求和以下用户补充确认，继续生成需求文档并推进后续 workflow。",
            "",
            "原始需求：",
            base_request,
            "",
            "用户补充确认：",
            answers_text,
        ]
    ).strip()


def _clarification_answers_to_text(value: Any) -> str:
    if isinstance(value, dict):
        lines: list[str] = []
        for key, answer in value.items():
            answer_text = _answer_to_text(answer)
            if answer_text:
                lines.append(f"- {key}: {answer_text}")
        return "\n".join(lines)

    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                question = _optional_text(item.get("question")) or _optional_text(
                    item.get("header")
                )
                answer = _answer_to_text(item.get("answer"))
                if question and answer:
                    lines.append(f"- {question}: {answer}")
            else:
                answer = _answer_to_text(item)
                if answer:
                    lines.append(f"- {answer}")
        return "\n".join(lines)

    return _answer_to_text(value)


def _answer_to_text(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return ", ".join(
            f"{key}={answer}" for key, answer in value.items() if str(answer).strip()
        )
    return str(value).strip() if value is not None else ""
