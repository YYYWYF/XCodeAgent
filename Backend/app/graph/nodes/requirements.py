from app.agents.main.requirements_analyzer import analyze_requirements_with_main_agent
from app.graph.nodes.confirmation import user_confirmed_text
from app.graph.state import ProjectState
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.spec_documents import (
    requirement_spec_json_path,
    write_requirement_spec_document,
)


def requirements(state: ProjectState) -> dict:
    existing_spec = state.get("requirement_spec")
    if existing_spec and _user_confirmed_requirement_spec(state.get("request", "")):
        spec = {
            **existing_spec,
            "confirmation_status": "confirmed",
        }
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

    analysis = analyze_requirements_with_main_agent(state["request"])
    spec = analysis["requirement_spec"]
    clarification = analysis["clarification"]
    if clarification["status"] == "clear":
        clarification = _requirement_spec_confirmation_payload(spec)
        spec["clarification_questions"] = clarification["questions"]
        spec["clarification_status"] = clarification["status"]
        spec["confirmation_status"] = "pending_user_confirmation"
    spec_path = write_requirement_spec_document(state, spec)

    return {
        "phase": "requirements",
        "status": clarification["status"],
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
