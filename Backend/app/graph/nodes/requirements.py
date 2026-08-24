import json
import re
import logging
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer

from app.agents.main.document_sync import sync_requirement_spec_from_markdown
from app.agents.main.requirements_analyzer import (
    MAX_REQUIREMENT_CLARIFICATION_ROUNDS,
    analyze_requirements_with_chat_model,
)
from app.graph.nodes.confirmation import (
    extract_confirmation_answer,
    user_confirmed_text,
    user_requested_changes_text,
)
from app.graph.state import ProjectState
from app.services.data_source_policy import (
    apply_authoritative_datasource_type,
    datasource_type_from_artifact,
)
from app.services.requirement_spec import apply_requirement_spec_editor_changes
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload, clear_clarification
from app.workspace.spec_documents import (
    confirmed_requirement_spec_json_path,
    edited_requirement_spec_markdown,
    requirement_spec_draft_json_path,
    requirement_spec_draft_markdown_path,
    requirement_spec_markdown_path,
    synchronize_requirement_spec_markdown_datasource_types,
    workspace_root,
    write_confirmed_requirement_spec_document,
    write_requirement_spec_draft_document,
)


logger = logging.getLogger("uvicorn.error")


def _clarification_round(state: dict) -> int:
    """读取并限制当前 RequirementSpec 已完成的澄清轮数。"""

    try:
        value = int(state.get("requirements_clarification_round", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return max(0, min(value, MAX_REQUIREMENT_CLARIFICATION_ROUNDS))


def _llm_token_callback(token: str) -> None:
    """将 LLM 流式 token 转发到 LangGraph custom stream。"""

    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        return
    writer({"type": "llm.token", "token": token, "node": "requirements"})


def confirm_requirement_spec_artifact(
    state: ProjectState,
    existing_spec: dict[str, Any],
    *,
    datasource_type: str,
) -> dict:
    """把待确认 RequirementSpec 提升为正式产物：同步用户编辑、写正式文档并返回确认更新。

    需求节点自身的确认分支与产品规划门的合并确认（一次确认需求+产品规划）共用本函数。
    """

    editor_changes = state.get("edited_requirement_spec")
    current_document_path = str(state.get("requirement_spec_path") or "").strip()
    has_current_document = bool(current_document_path) and Path(current_document_path).is_file()
    if isinstance(editor_changes, dict) and editor_changes:
        synchronized_spec = apply_requirement_spec_editor_changes(
            existing_spec,
            editor_changes,
            datasource_type=datasource_type,
        )
        edited_markdown = None
    else:
        # 用户可以直接编辑右侧草稿 Markdown；确认时只读取当前状态指向的文件，
        # 避免误读工作区里上一版正式文档。
        edited_markdown = (
            edited_requirement_spec_markdown(state, existing_spec)
            if has_current_document
            else None
        )
        synchronized_spec = (
            sync_requirement_spec_from_markdown(
                existing_spec,
                edited_markdown,
                datasource_type=datasource_type,
            )
            if edited_markdown is not None
            else existing_spec
        )
        synchronized_spec = apply_authoritative_datasource_type(
            synchronized_spec,
            datasource_type,
        )
    spec = {
        **synchronized_spec,
        "clarification_questions": [],
        "clarification_status": "clear",
        "confirmation_status": "confirmed",
    }
    markdown_path = requirement_spec_markdown_path(state)
    confirmed_markdown: str | None = None
    if not (isinstance(editor_changes, dict) and editor_changes) and has_current_document and markdown_path.is_file():
        markdown_content = markdown_path.read_text(encoding="utf-8")
        synchronized_markdown = synchronize_requirement_spec_markdown_datasource_types(
            markdown_content,
            spec,
        )
        if synchronized_markdown != markdown_content:
            markdown_path.write_text(synchronized_markdown, encoding="utf-8")
        confirmed_markdown = synchronized_markdown
    spec_path = write_confirmed_requirement_spec_document(
        state,
        spec,
        markdown=confirmed_markdown,
    )
    return {
        "phase": "requirements",
        "status": "completed",
        "requirement_spec": spec,
        "requirements_confirmed": True,
        "requirements_clarification_round": 0,
        "requirement_spec_path": spec_path,
        "requirement_spec_json_path": str(confirmed_requirement_spec_json_path(state)),
        "edited_requirement_spec": {},
        "requirement_spec_feedback": "",
        "clarification": _requirement_spec_confirmed_payload(spec),
        "timeline": ["requirements"],
    }


def requirements(state: ProjectState) -> dict:
    """生成、修订或确认 RequirementSpec，并始终执行应用数据源策略保护。"""

    # 应用级不再有数据源类型；需求阶段只保留实体字段展示信息，数据源由实体设计决定。
    datasource_type = datasource_type_from_artifact(
        state.get("requirement_spec") if isinstance(state.get("requirement_spec"), dict) else {},
        fallback="database",
    )
    existing_spec = state.get("requirement_spec")
    if isinstance(existing_spec, dict):
        existing_spec = apply_authoritative_datasource_type(existing_spec, datasource_type)
    interaction = _application_planning_interaction(state)
    application_planning_scope = state.get("workflow_scope") == "application_planning"
    request = _request_for_requirement_node(state, interaction)
    revision_requested = (
        interaction.get("action") == "revise"
        if application_planning_scope
        else _requirement_revision_requested(request)
    )
    clarification_round = _clarification_round(state)
    if not isinstance(existing_spec, dict) or (
        revision_requested
        and existing_spec.get("confirmation_status") != "pending_user_input"
    ):
        # 新需求或已确认需求的重新修订都开启新的三轮澄清预算。
        clarification_round = 0
    if (
        application_planning_scope
        and isinstance(existing_spec, dict)
        and existing_spec.get("confirmation_status") == "confirmed"
        and (
            not _has_application_planning_revision_context(state)
            and (not interaction or interaction.get("action") == "confirm")
        )
    ):
        # 已确认需求且没有新的结构化交互或修订游标时直接复用 checkpoint，禁止再次调用 LLM。
        return _confirmed_requirement_spec_update(state, existing_spec)
    if (
        existing_spec
        and existing_spec.get("confirmation_status") == "pending_user_confirmation"
        and not _has_explicit_user_submission(state)
    ):
        draft_path = str(state.get("requirement_spec_path") or requirement_spec_draft_markdown_path(state))
        draft_json_path = str(
            state.get("requirement_spec_json_path") or requirement_spec_draft_json_path(state)
        )
        return {
            "phase": "requirements",
            "status": "requires_user_input",
            "requirement_spec": existing_spec,
            "requirements_confirmed": False,
            "requirements_clarification_round": clarification_round,
            "requirement_spec_path": draft_path,
            "requirement_spec_json_path": draft_json_path,
            "clarification": _requirement_spec_confirmation_payload(existing_spec),
            "timeline": ["requirements"],
        }
    if (
        existing_spec
        and existing_spec.get("confirmation_status") == "pending_user_confirmation"
        and not revision_requested
        and (
            interaction.get("action") == "confirm"
            if application_planning_scope
            else _user_confirmed_requirement_spec(request)
        )
    ):
        return confirm_requirement_spec_artifact(
            state,
            existing_spec,
            datasource_type=datasource_type,
        )
    analysis_request = _requirement_analysis_request(
        request,
        existing_spec,
        interaction,
        application_planning_scope=application_planning_scope,
    )
    analysis = analyze_requirements_with_chat_model(
        analysis_request,
        existing_spec=existing_spec,
        datasource_type=datasource_type,
        clarification_round=clarification_round,
        on_token=_llm_token_callback,
    )
    spec = apply_authoritative_datasource_type(
        analysis["requirement_spec"],
        datasource_type,
    )
    _apply_menus_root_path_to_pages(spec, state)
    clarification = analysis["clarification"]
    clarification = _without_technical_datasource_questions(clarification, spec)
    clarification = _without_non_substantive_completeness_questions(
        clarification,
        spec,
    )
    next_clarification_round = clarification_round
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
        next_round = clarification_round + 1
        if clarification_round >= MAX_REQUIREMENT_CLARIFICATION_ROUNDS:
            # 用户已经回答完第三轮后，不再信任模型继续追问，直接进入正式需求确认。
            clarification = _requirement_spec_confirmation_payload(
                spec,
                clarification_limit_reached=True,
            )
            spec["clarification_questions"] = []
            spec["clarification_status"] = "clear"
            spec["confirmation_status"] = "pending_user_confirmation"
            status = clarification["status"]
        else:
            # 当前问题属于本轮最后一批；先展示给用户，下一次恢复只允许做最终合并。
            next_clarification_round = min(
                next_round,
                MAX_REQUIREMENT_CLARIFICATION_ROUNDS,
            )
            spec["confirmation_status"] = "pending_user_input"
            status = clarification["status"]

    if spec.get("confirmation_status") == "pending_user_input":
        # ask_user 期间只保留内存中的未完成事实，不能生成需求文档、页面占位或本地草稿。
        return {
            "phase": "requirements",
            "status": status,
            "requirement_spec": spec,
            "requirements_confirmed": False,
            "requirements_clarification_round": next_clarification_round,
            "requirement_spec_path": "",
            "requirement_spec_json_path": "",
            "clarification": clarification,
            "timeline": ["requirements"],
        }

    # 澄清已结束后才生成待确认草稿；用户确认时再由确认分支提升为正式文档。
    spec_path = write_requirement_spec_draft_document(state, spec)
    return {
        "phase": "requirements",
        "status": status,
        "requirement_spec": spec,
        "requirements_confirmed": False,
        "requirements_clarification_round": next_clarification_round,
        "requirement_spec_path": spec_path,
        "requirement_spec_json_path": str(requirement_spec_draft_json_path(state)),
        "clarification": clarification,
        "timeline": ["requirements"],
    }


def _requirement_spec_confirmation_payload(
    spec: dict,
    *,
    clarification_limit_reached: bool = False,
) -> dict:
    """构造需求确认卡，并在澄清预算用尽时明确告知用户。"""

    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="需求确认",
                question=(
                    "需求分析已完成，请审核当前需求内容。"
                    "如需补充或调整，请填写具体意见；明确确认后才会生成正式需求文档。"
                ),
                type="text",
                placeholder="例如：需要增加审批人角色和盘点页面。",
            )
        ]
    )
    payload["mode"] = "requirement_spec_confirmation"
    payload["message"] = (
        "已完成最多 3 轮需求澄清，请审核当前需求；如仍需调整，请在确认卡中填写具体意见。"
        if clarification_limit_reached
        else "需求分析已完成，请确认需求没问题后生成正式需求文档。"
    )
    if clarification_limit_reached:
        payload["clarification_limit_reached"] = True
    payload["spec_summary"] = spec.get("app_info", {}).get("name", "未命名应用")
    return payload


def _requirement_spec_confirmed_payload(spec: dict) -> dict:
    return {
        "mode": "requirement_spec_confirmation",
        "status": "clear",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": [],
        "assumptions": [],
        "message": "需求文档已由用户确认，可以继续产品规划。",
        "spec_summary": spec.get("app_info", {}).get("name", "未命名应用"),
    }


def _user_confirmed_requirement_spec(request: str) -> bool:
    if user_requested_changes_text(request):
        return False
    return user_confirmed_text(
        request,
        positive_signals=(
            "正确",
            "没问题",
            "继续规划",
            "可以继续",
            "无误",
            "确认",
            "好的",
            "好",
            "OK",
            "ok",
        ),
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
            "不好",
        ),
    )


def _application_planning_interaction(state: ProjectState) -> dict:
    """读取当前创建规划的结构化动作，其他 workflow 不走该分支。"""

    value = state.get("application_planning_interaction")
    return value if isinstance(value, dict) and value else {}


def _request_for_requirement_node(
    state: ProjectState,
    interaction: dict,
) -> str:
    """为需求节点选择结构化交互中的明确请求，避免用阶段文本猜动作。"""

    if state.get("workflow_scope") == "application_planning" and interaction:
        return str(interaction.get("request") or "").strip()
    return str(state.get("request") or "")


def _has_application_planning_revision_context(state: ProjectState) -> bool:
    """判断创建规划 checkpoint 是否带有尚未消费的修订上下文。"""

    return any(
        bool(state.get(field))
        for field in (
            "design_change_submission",
            "design_change_request",
            "design_change_generation_target",
            "design_change_generation_request",
            "design_interaction_origin",
        )
    )


def _confirmed_requirement_spec_update(
    state: ProjectState,
    spec: dict,
) -> dict:
    """构造已确认需求的早退状态，不触发文档重写或模型分析。"""

    datasource_type = datasource_type_from_artifact(spec, fallback="database")
    confirmed_spec = apply_authoritative_datasource_type(spec, datasource_type)
    return {
        "phase": "requirements",
        "status": "completed",
        "requirement_spec": confirmed_spec,
        "requirements_confirmed": True,
        "requirements_clarification_round": 0,
        "requirement_spec_path": str(state.get("requirement_spec_path") or ""),
        "requirement_spec_json_path": str(state.get("requirement_spec_json_path") or ""),
        "clarification": _requirement_spec_confirmed_payload(confirmed_spec),
        "timeline": ["requirements"],
    }


def _has_explicit_user_submission(state: ProjectState) -> bool:
    """创建规划只接受原生中断恢复写入的显式交互，其他调用保持原行为。"""

    return (
        state.get("workflow_scope") != "application_planning"
        or bool(state.get("application_planning_interaction"))
    )


def _requirement_revision_requested(request: str) -> bool:
    """只根据本轮确认答案判断是否需要修订需求。"""

    return user_requested_changes_text(request)


def _requirement_analysis_request(
    request: str,
    existing_spec: dict | None,
    interaction: dict | None = None,
    *,
    application_planning_scope: bool = False,
) -> str:
    """把本轮待确认文档上的修改性意见提升为需求修订请求。"""

    if application_planning_scope:
        # application_planning 的分支已由审阅门明确给出，原文不能再经过关键词分类。
        return request
    revision_feedback = extract_confirmation_answer(request).strip() or request
    if (
        not isinstance(existing_spec, dict)
        or existing_spec.get("confirmation_status") != "pending_user_confirmation"
        or not user_requested_changes_text(request)
    ):
        return request
    return "\n".join(
        [
            "用户正在审核已生成的需求文档，并提出了以下修改意见。",
            "请基于现有 RequirementSpec 和这段最新意见重新生成完整需求文档。",
            "最新修改意见优先覆盖冲突的旧需求；不要把本次意见当作确认通过。",
            "",
            "用户修改意见：",
            revision_feedback,
        ]
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
    requirement_dimensions = ("角色", "页面", "菜单", "功能", "模块", "验收")
    return any(marker in normalized for marker in additive_markers) and any(
        dimension in normalized for dimension in requirement_dimensions
    )


def _without_technical_datasource_questions(clarification: dict, spec: dict) -> dict:
    """移除产品需求阶段误生成的数据源技术问题，并在无产品问题时直接清空澄清。"""

    questions = clarification.get("questions")
    if not isinstance(questions, list):
        return clarification
    filtered = [question for question in questions if not _is_datasource_question(question)]
    if not filtered:
        return clear_clarification(spec)
    return {**clarification, "questions": filtered}


def _without_non_substantive_completeness_questions(
    clarification: dict,
    spec: dict,
) -> dict:
    """忽略模型发出的泛化完整性确认，让正式产物确认卡承担确认职责。"""

    questions = clarification.get("questions")
    if not isinstance(questions, list):
        return clarification
    filtered = [
        question
        for question in questions
        if not _is_non_substantive_completeness_question(question)
    ]
    if not filtered:
        return clear_clarification(spec)
    return {**clarification, "questions": filtered}


def _is_non_substantive_completeness_question(question: object) -> bool:
    """识别“需求是否完整/无需进一步澄清”这类没有新增信息的问题。"""

    if not isinstance(question, dict):
        return False
    text = "".join(
        str(question.get(key) or "")
        for key in ("id", "header", "dimension", "question")
    )
    normalized = re.sub(r"[\s\u3000，。！？；：、,.!?;:]+", "", text).lower()
    generic_markers = (
        "请确认需求已完整无需进一步澄清",
        "请确认需求是否完整无需进一步澄清",
        "需求已完整无需进一步澄清",
        "需求是否完整无需进一步澄清",
        "无需进一步澄清",
        "不需要进一步澄清",
        "是否还需要进一步澄清",
        "是否还需要澄清",
    )
    return any(marker in normalized for marker in generic_markers)


def _is_datasource_question(question: object) -> bool:
    """识别数据源、数据库、存储与持久化类技术澄清问题。"""

    if not isinstance(question, dict):
        return False
    text = "".join(
        str(question.get(key) or "")
        for key in ("id", "header", "dimension", "question")
    ).replace(" ", "")
    return any(marker in text for marker in ("数据源", "数据库", "存储方式", "持久化"))
