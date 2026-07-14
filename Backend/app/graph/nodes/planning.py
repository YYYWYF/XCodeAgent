from app.agents.main.document_sync import sync_project_plan_from_markdown
from app.agents.main.planner import (
    plan_project_with_chat_model,
    revise_project_plan_with_chat_model,
)
from app.agents.main.page_designer import (
    design_data_source_with_chat_model,
    design_page_with_chat_model,
)
from app.graph.nodes.confirmation import user_confirmed_text
from app.graph.state import ProjectState
from app.services.detail_review import (
    apply_detail_review_submission,
    detail_review_payload,
)
from app.services.project_plan import apply_project_plan_feedback
from app.services.page_detail_plan import (
    attach_data_source_detail_plan,
    attach_page_detail_plan,
    detail_design_targets,
    extract_page_detail_context,
)
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.plan_documents import (
    edited_project_plan_markdown,
    project_plan_json_path,
    project_plan_markdown_path,
    write_project_plan_document,
    write_project_plan_json,
)


def project_planning(state: ProjectState) -> dict:
    if state.get("project_plan") and _user_confirmed_project_plan(
        state.get("request", "")
    ):
        edited_markdown = edited_project_plan_markdown(
            state,
            state["project_plan"],
        )
        synchronized_plan = (
            sync_project_plan_from_markdown(
                state["project_plan"],
                state.get("requirement_spec", {}),
                edited_markdown,
            )
            if edited_markdown is not None
            else state["project_plan"]
        )
        project_plan = {
            **apply_project_plan_feedback(
                synchronized_plan,
                state.get("request", ""),
            ),
            "confirmation_status": "confirmed",
        }
        markdown_path = project_plan_markdown_path(state)
        if markdown_path.is_file():
            project_plan_path = str(markdown_path)
            write_project_plan_json(state, project_plan)
        else:
            project_plan_path = write_project_plan_document(state, project_plan)
        return {
            "phase": "project_planning",
            "status": "completed",
            "project_plan": project_plan,
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "clarification": _project_plan_confirmed_payload(project_plan),
            "timeline": ["project_planning"],
        }

    requirement_spec = state["requirement_spec"]
    if state.get("project_plan") and state.get("request"):
        requirement_spec = {
            **requirement_spec,
            "planning_adjustment_request": state["request"],
        }
    project_plan = plan_project_with_chat_model(
        requirement_spec,
        **(
            {"existing_plan": state["project_plan"]}
            if state.get("project_plan")
            else {}
        ),
    )
    project_plan = apply_project_plan_feedback(
        project_plan,
        state.get("request", ""),
    )
    project_plan["confirmation_status"] = "pending_user_confirmation"
    project_plan_path = write_project_plan_document(state, project_plan)
    clarification = _project_plan_confirmation_payload(project_plan)

    return {
        "phase": "project_planning",
        "status": "requires_user_input",
        "project_plan": project_plan,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": _project_plan_json_path_for_state(state),
        "clarification": clarification,
        "timeline": ["project_planning"],
    }


def detail_confirmation(state: ProjectState) -> dict:
    pending_plan = state.get("pending_project_plan")
    submission = state.get("detail_review_submission")
    if pending_plan and isinstance(submission, dict):
        edited_markdown = edited_project_plan_markdown(state, pending_plan)
        synchronized_plan = (
            sync_project_plan_from_markdown(
                pending_plan,
                state.get("requirement_spec", {}),
                edited_markdown,
            )
            if edited_markdown is not None and state.get("requirement_spec")
            else pending_plan
        )
        confirmed_plan = apply_detail_review_submission(
            synchronized_plan,
            submission,
        )
        project_plan_path = write_project_plan_document(state, confirmed_plan)
        return {
            "phase": "detail_confirmation",
            "status": "completed",
            "project_plan": confirmed_plan,
            "pending_project_plan": {},
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "clarification": _project_plan_confirmed_payload(confirmed_plan),
            "detail_selection": {
                "status": "completed",
                "mode": "batch_review",
                "targets": [],
            },
            "detail_plans": [
                *confirmed_plan.get("page_detail_plans", []),
                *confirmed_plan.get("data_source_detail_plans", []),
            ],
            "detail_review_submission": {},
            "timeline": ["detail_confirmation"],
        }

    if pending_plan and _user_confirmed_project_plan(state.get("request", "")):
        legacy_submission = {
            "review_status": "confirmed",
            "target_changes": [],
            "overall_note": "legacy text confirmation",
        }
        return detail_confirmation(
            {**state, "detail_review_submission": legacy_submission}
        )

    if pending_plan:
        revised_plan = revise_project_plan_with_chat_model(
            pending_plan,
            state.get("request", ""),
        )
        revised_plan = _generate_all_detail_plans(revised_plan)
        revised_plan["confirmation_status"] = "pending_user_confirmation"
        project_plan_path = write_project_plan_document(state, revised_plan)
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": detail_review_payload(revised_plan),
            "pending_project_plan": revised_plan,
            "project_plan": state.get("project_plan"),
            "project_plan_path": project_plan_path,
            "project_plan_json_path": _project_plan_json_path_for_state(state),
            "detail_selection": {
                "status": "requires_user_input",
                "mode": "batch_review",
                "targets": detail_design_targets(revised_plan),
            },
            "timeline": ["detail_confirmation"],
        }

    pending_plan = _generate_all_detail_plans(state["project_plan"])
    pending_plan["confirmation_status"] = "pending_user_confirmation"
    project_plan_path = write_project_plan_document(state, pending_plan)
    targets = detail_design_targets(pending_plan)
    return {
        "phase": "detail_confirmation",
        "status": "requires_user_input",
        "clarification": detail_review_payload(pending_plan),
        "pending_project_plan": pending_plan,
        "project_plan": state["project_plan"],
        "project_plan_path": project_plan_path,
        "project_plan_json_path": _project_plan_json_path_for_state(state),
        "detail_selection": {
            "status": "requires_user_input",
            "mode": "batch_review",
            "targets": targets,
        },
        "detail_plans": [
            *pending_plan.get("page_detail_plans", []),
            *pending_plan.get("data_source_detail_plans", []),
        ],
        "timeline": ["detail_confirmation"],
    }


def _generate_all_detail_plans(project_plan: dict) -> dict:
    updated_plan = project_plan
    for source in project_plan.get("data_sources", []):
        source_id = source.get("id") if isinstance(source, dict) else None
        if not source_id:
            continue
        detail = design_data_source_with_chat_model(updated_plan, source_id, "")
        detail["status"] = "pending_user_confirmation"
        detail["approved"] = False
        updated_plan = attach_data_source_detail_plan(updated_plan, detail)

    for page in project_plan.get("frontend_pages", []):
        page_id = page.get("id") if isinstance(page, dict) else None
        if not page_id:
            continue
        page_context = extract_page_detail_context(updated_plan, page_id)
        detail = design_page_with_chat_model(updated_plan, page_context)
        detail["status"] = "pending_user_confirmation"
        detail["approved"] = False
        updated_plan = attach_page_detail_plan(updated_plan, detail)

    updated_plan["detail_confirmation_summary"] = {
        "confirmed_pages": 0,
        "confirmed_data_sources": 0,
        "total_pages": len(updated_plan.get("frontend_pages", [])),
        "total_data_sources": len(updated_plan.get("data_sources", [])),
        "mode": "batch_review",
    }
    for page in updated_plan.get("frontend_pages", []):
        if isinstance(page, dict):
            page["detail_status"] = "pending_user_confirmation"
    for source in updated_plan.get("data_sources", []):
        if isinstance(source, dict):
            source["detail_status"] = "pending_user_confirmation"
    return updated_plan


def _project_plan_json_path_for_state(state: ProjectState) -> str:
    return str(state.get("project_plan_json_path") or project_plan_json_path(state))


def _project_plan_confirmation_payload(project_plan: dict) -> dict:
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="计划确认",
                question=(
                    "请确认已生成的项目规划书是否正确。"
                    "如果正确，请回复“正确，继续”；"
                    "如果需要调整，请直接写出要修改的架构、API、页面、数据源、权限或验收标准。"
                ),
                type="text",
                placeholder="例如：正确，继续 / 需要增加库存盘点页面和盘点记录数据源。",
            )
        ]
    )
    payload["mode"] = "project_plan_confirmation"
    payload["message"] = "请确认 ProjectPlan 后再继续页面/数据源细节设计。"
    payload["plan_summary"] = project_plan.get("app", {}).get("name", "未命名应用")
    return payload


def _project_plan_confirmed_payload(project_plan: dict) -> dict:
    return {
        "mode": "project_plan_confirmation",
        "status": "clear",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": [],
        "assumptions": [],
        "message": "ProjectPlan 已由用户确认，可以继续后续 workflow。",
        "plan_summary": project_plan.get("app", {}).get("name", "未命名应用"),
    }


def _user_confirmed_project_plan(request: str) -> bool:
    return user_confirmed_text(
        request,
        positive_signals=("正确", "没问题", "继续", "可以继续", "无误"),
        negative_signals=("不正确", "需要修改", "修改", "调整", "补充", "不对"),
    )
