from app.agents.main.document_sync import sync_requirement_spec_from_markdown
from app.agents.main.requirements_analyzer import analyze_requirements_with_chat_model
from app.graph.nodes.confirmation import user_confirmed_text
from app.graph.state import ProjectState
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload, clear_clarification
from app.workspace.spec_documents import (
    edited_requirement_spec_markdown,
    requirement_spec_json_path,
    requirement_spec_markdown_path,
    write_requirement_spec_document,
    write_requirement_spec_json,
)


def requirements(state: ProjectState) -> dict:
    existing_spec = state.get("requirement_spec")
    if (
        existing_spec
        and existing_spec.get("confirmation_status") == "pending_user_confirmation"
        and _user_confirmed_requirement_spec(state.get("request", ""))
    ):
        edited_markdown = edited_requirement_spec_markdown(state, existing_spec)
        synchronized_spec = (
            sync_requirement_spec_from_markdown(existing_spec, edited_markdown)
            if edited_markdown is not None
            else existing_spec
        )
        spec = {
            **synchronized_spec,
            "clarification_questions": [],
            "clarification_status": "clear",
            "confirmation_status": "confirmed",
        }
        markdown_path = requirement_spec_markdown_path(state)
        if markdown_path.is_file():
            spec_path = str(markdown_path)
            write_requirement_spec_json(state, spec)
        else:
            spec_path = write_requirement_spec_document(state, spec)
        return {
            "phase": "requirements",
            "status": "completed",
            "requirement_spec": spec,
            "requirement_spec_path": spec_path,
            "requirement_spec_json_path": str(requirement_spec_json_path(state)),
            "clarification": _requirement_spec_confirmed_payload(spec),
            "timeline": ["requirements"],
        }
    analysis = analyze_requirements_with_chat_model(
        state["request"],
        existing_spec=existing_spec,
        allow_clarification=state.get("workflow_scope") != "application_planning",
    )
    spec = analysis["requirement_spec"]
    clarification = analysis["clarification"]
    if _should_suppress_repeat_clarification(existing_spec, clarification):
        clarification = clear_clarification(spec)
        spec["clarification_questions"] = []
        spec["clarification_status"] = "clear"
    if clarification["status"] == "clear":
        clarification = _requirement_spec_confirmation_payload(spec)
        spec["clarification_questions"] = []
        spec["clarification_status"] = "clear"
        spec["confirmation_status"] = "pending_user_confirmation"
        status = clarification["status"]
    else:
        spec["confirmation_status"] = "pending_user_input"
        status = clarification["status"]
    spec_path = write_requirement_spec_document(state, spec)

    return {
        "phase": "requirements",
        "status": status,
        "requirement_spec": spec,
        "requirement_spec_path": spec_path,
        "requirement_spec_json_path": str(requirement_spec_json_path(state)),
        "clarification": clarification,
        "timeline": ["requirements"],
    }


def _requirement_spec_confirmation_payload(spec: dict) -> dict:
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="需求确认",
                question=(
                    "请确认已生成的需求文档是否正确。"
                    "如果正确，请回复“正确，继续规划”；"
                    "如果需要修改，请直接写出要调整的应用信息、角色、功能、页面、数据源、流程或验收标准。"
                ),
                type="text",
                placeholder="例如：正确，继续规划 / 需要增加审批人角色和盘点页面。",
            )
        ]
    )
    payload["mode"] = "requirement_spec_confirmation"
    payload["message"] = "请确认 RequirementSpec 是否正确后再继续项目规划。"
    payload["spec_summary"] = spec.get("app_info", {}).get("name", "未命名应用")
    return payload


def _requirement_spec_confirmed_payload(spec: dict) -> dict:
    return {
        "mode": "requirement_spec_confirmation",
        "status": "clear",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": [],
        "assumptions": spec.get("assumptions", []),
        "message": "RequirementSpec 已由用户确认，可以继续项目规划。",
        "spec_summary": spec.get("app_info", {}).get("name", "未命名应用"),
    }


def _user_confirmed_requirement_spec(request: str) -> bool:
    return user_confirmed_text(
        request,
        positive_signals=("正确", "没问题", "继续规划", "可以继续", "无误"),
        negative_signals=("不正确", "需要修改", "修改", "调整", "补充", "不对"),
    )


def _should_suppress_repeat_clarification(
    existing_spec: dict | None,
    clarification: dict,
) -> bool:
    if not isinstance(existing_spec, dict):
        return False
    if existing_spec.get("confirmation_status") != "pending_user_input":
        return False
    if clarification.get("status") != "requires_user_input":
        return False

    questions = clarification.get("questions")
    if not isinstance(questions, list) or not questions:
        return False

    return all(_is_optional_additive_question(question) for question in questions)


def _is_optional_additive_question(question: object) -> bool:
    if not isinstance(question, dict):
        return False
    text = "".join(
        str(question.get(key) or "")
        for key in ("id", "header", "dimension", "question")
    )
    normalized = text.replace(" ", "")
    additive_markers = ("其他", "更多", "还有", "补充", "是否还", "是否有")
    requirement_dimensions = ("角色", "页面", "菜单", "功能", "模块", "数据源", "验收")
    return any(marker in normalized for marker in additive_markers) and any(
        dimension in normalized for dimension in requirement_dimensions
    )
