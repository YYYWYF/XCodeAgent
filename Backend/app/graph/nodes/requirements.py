import json
import re
import logging

from langgraph.config import get_stream_writer

from app.agents.main.document_sync import sync_requirement_spec_from_markdown
from app.agents.main.requirements_analyzer import analyze_requirements_with_chat_model
from app.graph.nodes.confirmation import user_confirmed_text
from app.graph.state import ProjectState
from app.services.requirement_spec import apply_requirement_spec_editor_changes
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload, clear_clarification
from app.workspace.spec_documents import (
    edited_requirement_spec_markdown,
    requirement_spec_json_path,
    requirement_spec_markdown_path,
    workspace_root,
    write_requirement_spec_document,
    write_requirement_spec_json,
)


logger = logging.getLogger("uvicorn.error")


def _llm_token_callback(token: str) -> None:
    """将 LLM 流式 token 转发到 LangGraph custom stream。"""

    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        return
    writer({"type": "llm.token", "token": token, "node": "requirements"})


def requirements(state: ProjectState) -> dict:
    existing_spec = state.get("requirement_spec")
    if (
        existing_spec
        and existing_spec.get("confirmation_status") == "pending_user_confirmation"
        and _user_confirmed_requirement_spec(state.get("request", ""))
    ):
        editor_changes = state.get("edited_requirement_spec")
        if isinstance(editor_changes, dict):
            synchronized_spec = apply_requirement_spec_editor_changes(
                existing_spec,
                editor_changes,
            )
            edited_markdown = None
        else:
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
            **(
                {"confirmation_feedback": state["requirement_spec_feedback"].strip()}
                if isinstance(state.get("requirement_spec_feedback"), str)
                and state["requirement_spec_feedback"].strip()
                else {}
            ),
        }
        markdown_path = requirement_spec_markdown_path(state)
        if isinstance(editor_changes, dict):
            spec_path = write_requirement_spec_document(state, spec)
        elif markdown_path.is_file():
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
            "edited_requirement_spec": {},
            "requirement_spec_feedback": "",
            "clarification": _requirement_spec_confirmed_payload(spec),
            "timeline": ["requirements"],
        }
    analysis = analyze_requirements_with_chat_model(
        state["request"],
        existing_spec=existing_spec,
        on_token=_llm_token_callback,
    )
    spec = analysis["requirement_spec"]
    _apply_menus_root_path_to_pages(spec, state)
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
                    "请审核已生成的需求文档。"
                    "如需调整，可填写修改意见；直接确认则表示文档正确并继续规划。"
                ),
                type="text",
                placeholder="例如：需要增加审批人角色和盘点页面。",
            )
        ]
    )
    payload["mode"] = "requirement_spec_confirmation"
    payload["message"] = "请确认需求文档是否正确后再继续项目规划。"
    payload["spec_summary"] = spec.get("app_info", {}).get("name", "未命名应用")
    return payload


def _requirement_spec_confirmed_payload(spec: dict) -> dict:
    return {
        "mode": "requirement_spec_confirmation",
        "status": "clear",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": [],
        "assumptions": spec.get("assumptions", []),
        "message": "需求文档已由用户确认，可以继续项目规划。",
        "spec_summary": spec.get("app_info", {}).get("name", "未命名应用"),
    }


def _user_confirmed_requirement_spec(request: str) -> bool:
    return user_confirmed_text(
        request,
        positive_signals=("正确", "没问题", "继续规划", "可以继续", "无误", "确认"),
        negative_signals=(
            "不正确",
            "需要修改",
            "要修改",
            "请修改",
            "想修改",
            "修改一下",
            "去修改",
            "重新修改",
            "需要调整",
            "要调整",
            "请调整",
            "调整一下",
            "需要补充",
            "要补充",
            "请补充",
            "补充一下",
            "不对",
        ),
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


def _apply_menus_root_path_to_pages(spec: dict, state: ProjectState) -> None:
    """从 application.json 读取 menus.rootPath 并拼接到所有页面路由前。"""
    try:
        app_file = workspace_root(state) / ".xcodeagent" / "application.json"
        if not app_file.is_file():
            return
        app_config = json.loads(app_file.read_text(encoding="utf-8"))
        root_path = str((app_config.get("menus") or {}).get("rootPath", "") or "/").strip()
        menus_enabled = bool((app_config.get("menus") or {}).get("enable"))
    except Exception:
        return

    app_info = spec.get("app_info") if isinstance(spec.get("app_info"), dict) else {}
    app_info["menu_enabled"] = menus_enabled
    if not root_path or root_path == "/":
        spec["app_info"] = app_info
        return
    root_path = root_path.rstrip("/")
    app_info["route_root_path"] = root_path
    spec["app_info"] = app_info
    for page in spec.get("pages", []):
        if isinstance(page, dict) and page.get("path"):
            page_path = str(page["path"]).strip()
            if menus_enabled and page_path == "/":
                page["path"] = root_path + _menu_home_leaf_path(page)
            elif page_path.startswith("/"):
                page["path"] = root_path + page_path
            else:
                page["path"] = root_path + "/" + page_path


def _menu_home_leaf_path(page: dict) -> str:
    """为启用菜单时的首页类页面生成非根路径的叶子路由。"""

    page_id = str(page.get("pageId") or page.get("id") or "").strip()
    route = re.sub(r"[^a-zA-Z0-9_-]+", "-", page_id or "home").strip("-_")
    route = route.replace("_", "-").lower() or "home"
    if route.endswith("-page") and route != "dashboard-page":
        route = route[: -len("-page")] or route
    if route in {"dashboard", "dashboard-page", "home", "index"}:
        route = "home"
    return f"/{route}"


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
