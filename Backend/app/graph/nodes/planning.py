from app.agents.main.document_sync import sync_project_plan_from_markdown
from app.agents.main.planner import (
    plan_project_with_chat_model,
    revise_project_plan_with_chat_model,
)
from app.agents.main.page_designer import (
    PageDependencyGapError,
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
from app.services.page_dependencies import page_data_source_ids, validate_project_plan_dependencies
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
        dependency_errors = validate_project_plan_dependencies(project_plan)
        if dependency_errors:
            repaired_plan, remaining_errors = _repair_project_plan_dependencies(
                project_plan,
                dependency_errors,
            )
            repaired_path = write_project_plan_document(state, repaired_plan)
            return {
                "phase": "project_planning",
                "status": "requires_user_input",
                "project_plan": repaired_plan,
                "project_plan_path": repaired_path,
                "project_plan_json_path": _project_plan_json_path_for_state(state),
                "clarification": (
                    _project_plan_dependency_error_payload(remaining_errors)
                    if remaining_errors
                    else _project_plan_confirmation_payload(repaired_plan)
                ),
                "timeline": ["project_planning"],
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
    dependency_errors = validate_project_plan_dependencies(project_plan)
    if dependency_errors:
        project_plan, dependency_errors = _repair_project_plan_dependencies(
            project_plan,
            dependency_errors,
        )
    project_plan_path = write_project_plan_document(state, project_plan)
    clarification = (
        _project_plan_dependency_error_payload(dependency_errors)
        if dependency_errors
        else _project_plan_confirmation_payload(project_plan)
    )

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
    """基于完整 ProjectPlan 和初始页面功能概览生成批量细节确认。"""

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

    project_plan = state.get("project_plan")
    if not isinstance(project_plan, dict):
        raise ValueError(
            "主 Workflow 需要工作区 .xcodeagent/plans/project-plan.json "
            "（兼容 plans/project-plan.json）作为初始输入。"
        )
    try:
        pending_plan = _generate_all_detail_plans(
            project_plan,
            frontend_pages=state.get("frontend_pages"),
            selected_page_id=state.get("selected_page_id"),
        )
    except PageDependencyGapError as exc:
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "clarification": _project_plan_revision_required_payload(str(exc)),
            "timeline": ["detail_confirmation"],
        }
    pending_plan["confirmation_status"] = "pending_user_confirmation"
    project_plan_path = write_project_plan_document(state, pending_plan)
    targets = detail_design_targets(pending_plan)
    return {
        "phase": "detail_confirmation",
        "status": "requires_user_input",
        "clarification": detail_review_payload(pending_plan),
        "pending_project_plan": pending_plan,
        "project_plan": project_plan,
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


def _generate_all_detail_plans(
    project_plan: dict,
    *,
    frontend_pages: list[dict] | None = None,
    selected_page_id: str | None = None,
) -> dict:
    """为计划中的数据源及用户选中的初始页面生成功能详细设计。"""

    updated_plan = project_plan
    pages = frontend_pages if isinstance(frontend_pages, list) else project_plan.get(
        "frontend_pages", []
    )
    if selected_page_id:
        pages = [
            page
            for page in pages
            if isinstance(page, dict) and page.get("id") == selected_page_id
        ]
        if not pages:
            raise ValueError(f"ProjectPlan 中不存在页面：{selected_page_id}")
    selected_source_ids = {
        str(source_id)
        for page in pages if isinstance(page, dict)
        for source_id in page_data_source_ids(
            page,
            [contract for contract in project_plan.get("api_contracts", []) if isinstance(contract, dict)],
        )
        if source_id
    }
    for source in project_plan.get("data_sources", []):
        source_id = source.get("id") if isinstance(source, dict) else None
        if not source_id or (selected_page_id and str(source_id) not in selected_source_ids):
            continue
        detail = design_data_source_with_chat_model(updated_plan, source_id, "")
        detail["status"] = "pending_user_confirmation"
        detail["approved"] = False
        updated_plan = attach_data_source_detail_plan(updated_plan, detail)

    for page in pages:
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
        "total_pages": len(pages),
        "total_data_sources": len(updated_plan.get("data_sources", [])),
        "mode": "batch_review",
    }
    selected_page_ids = {
        str(page.get("id")) for page in pages if isinstance(page, dict) and page.get("id")
    }
    for page in updated_plan.get("frontend_pages", []):
        if isinstance(page, dict) and (
            not selected_page_id or str(page.get("id")) in selected_page_ids
        ):
            page["detail_status"] = "pending_user_confirmation"
    for source in updated_plan.get("data_sources", []):
        if isinstance(source, dict) and (
            not selected_page_id or str(source.get("id")) in selected_source_ids
        ):
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


def _project_plan_dependency_error_payload(errors: list[str]) -> dict:
    """要求用户回到 ProjectPlan 修订页面依赖、路由或跳转缺口。"""

    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="计划依赖校验",
                question="系统已自动尝试修复 ProjectPlan 页面依赖，但仍有无法安全推断的问题。请补充业务决策后，我会重新生成 ProjectPlan；无需手动编辑 JSON。",
                type="text",
                placeholder="例如：为入职表单补充 create endpoint，并修正页面路由。",
            )
        ]
    )
    payload["mode"] = "project_plan_dependency_validation_error"
    payload["message"] = "ProjectPlan 自动修复后仍未通过依赖校验，页面设计未开始。"
    payload["errors"] = errors
    return payload


def _repair_project_plan_dependencies(
    project_plan: dict,
    errors: list[str],
) -> tuple[dict, list[str]]:
    """把确定性校验错误回灌给规划模型，最多自动修订一次页面依赖。"""

    feedback = "系统依赖校验失败，请在本次重新生成中完整修复以下问题：\n" + "\n".join(
        f"- {error}" for error in errors
    )
    repaired = revise_project_plan_with_chat_model(project_plan, feedback)
    repaired["confirmation_status"] = "pending_user_confirmation"
    return repaired, validate_project_plan_dependencies(repaired)


def _project_plan_revision_required_payload(reason: str) -> dict:
    """页面设计发现依赖缺口时阻止自由扩展，并要求修订 ProjectPlan。"""

    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="需要修订计划",
                question="页面设计需要尚未声明的 endpoint 或跳转目标，不能自由添加。请返回 ProjectPlan 修订依赖后重新确认。",
                type="text",
                placeholder="例如：在入职页面的 endpoint_dependencies 中补充员工创建接口。",
            )
        ]
    )
    payload["mode"] = "project_plan_revision_required"
    payload["message"] = "页面设计已停止，必须先修订并重新确认 ProjectPlan。"
    payload["reason"] = reason
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
