from app.agents.main.planner import plan_project_with_main_agent
from app.agents.main.page_designer import design_page_with_main_agent
from app.graph.nodes.confirmation import user_confirmed_text
from app.graph.state import ProjectState
from app.services.page_detail_plan import (
    apply_page_spec_answers,
    attach_data_source_detail_plan,
    attach_page_detail_plan,
    create_data_source_detail_plan,
    create_page_spec_from_project_plan,
    detail_design_targets,
    missing_page_spec_aspects,
    resolve_detail_design_target,
)
from app.tools.ask_user import AskUserOption, AskUserQuestion, build_ask_user_payload
from app.workspace.plan_documents import (
    project_plan_json_path,
    write_project_plan_document,
)


def project_planning(state: ProjectState) -> dict:
    if state.get("project_plan") and _user_confirmed_project_plan(state.get("request", "")):
        project_plan = {
            **state["project_plan"],
            "confirmation_status": "confirmed",
        }
        project_plan_path = write_project_plan_document(state, project_plan)
        return {
            "phase": "project_planning",
            "status": "completed",
            "project_plan": project_plan,
            "project_plan_path": project_plan_path,
            "project_plan_json_path": str(project_plan_json_path(state)),
            "clarification": _project_plan_confirmed_payload(project_plan),
            "timeline": ["project_planning"],
        }

    requirement_spec = state["requirement_spec"]
    if state.get("project_plan") and state.get("request"):
        requirement_spec = {
            **requirement_spec,
            "planning_adjustment_request": state["request"],
        }
    project_plan = plan_project_with_main_agent(requirement_spec)
    project_plan["confirmation_status"] = "pending_user_confirmation"
    project_plan_path = write_project_plan_document(state, project_plan)
    clarification = _project_plan_confirmation_payload(project_plan)

    return {
        "phase": "project_planning",
        "status": "requires_user_input",
        "project_plan": project_plan,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": str(project_plan_json_path(state)),
        "clarification": clarification,
        "timeline": ["project_planning"],
    }


def detail_confirmation(state: ProjectState) -> dict:
    if state.get("pending_project_plan") and _user_confirmed_project_plan(
        state.get("request", "")
    ):
        project_plan = {
            **state["pending_project_plan"],
            "confirmation_status": "confirmed",
        }
        project_plan_path = write_project_plan_document(state, project_plan)
        return {
            "phase": "detail_confirmation",
            "status": "completed",
            "project_plan": project_plan,
            "project_plan_path": project_plan_path,
            "project_plan_json_path": str(project_plan_json_path(state)),
            "clarification": _project_plan_confirmed_payload(project_plan),
            "detail_selection": state.get("detail_selection"),
            "selected_page_id": state.get("selected_page_id"),
            "selected_data_source_id": state.get("selected_data_source_id"),
            "confirmed_page_spec": state.get("confirmed_page_spec"),
            "detail_plans": state.get("detail_plans", []),
            "timeline": ["detail_confirmation"],
        }

    if state.get("pending_project_plan"):
        clarification = _project_plan_adjustment_confirmation_payload(
            state["pending_project_plan"]
        )
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": clarification,
            "pending_project_plan": state["pending_project_plan"],
            "project_plan": state.get("project_plan"),
            "project_plan_path": state.get("project_plan_path"),
            "project_plan_json_path": state.get("project_plan_json_path"),
            "detail_selection": state.get("detail_selection"),
            "selected_page_id": state.get("selected_page_id"),
            "selected_data_source_id": state.get("selected_data_source_id"),
            "confirmed_page_spec": state.get("confirmed_page_spec"),
            "detail_plans": state.get("detail_plans", []),
            "timeline": ["detail_confirmation"],
        }

    project_plan = state["project_plan"]
    target = resolve_detail_design_target(
        project_plan,
        state.get("request", ""),
        selected_page_id=state.get("selected_page_id"),
        selected_data_source_id=state.get("selected_data_source_id"),
    )

    if target is None:
        clarification = _detail_target_selection(project_plan)
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": clarification,
            "detail_selection": {
                "status": "requires_user_input",
                "targets": detail_design_targets(project_plan),
            },
            "project_plan": project_plan,
            "project_plan_path": state.get("project_plan_path"),
            "project_plan_json_path": state.get("project_plan_json_path"),
            "timeline": ["detail_confirmation"],
        }

    if target["type"] != "page":
        data_source_detail_plan = create_data_source_detail_plan(
            project_plan,
            target["id"],
            user_request=state.get("request", ""),
        )
        pending_project_plan = attach_data_source_detail_plan(
            project_plan,
            data_source_detail_plan,
        )
        pending_project_plan["confirmation_status"] = "pending_user_confirmation"
        project_plan_path = write_project_plan_document(state, pending_project_plan)
        clarification = _project_plan_adjustment_confirmation_payload(
            pending_project_plan
        )
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": clarification,
            "detail_selection": {
                "status": "selected",
                "selected_target": target,
            },
            "selected_data_source_id": target["id"],
            "data_source_spec_confirmation": {
                "status": "confirmed",
                "confirmed_data_source_spec": data_source_detail_plan,
                "source": "project_plan_with_user_confirmation",
            },
            "detail_plans": [data_source_detail_plan],
            "project_plan": project_plan,
            "pending_project_plan": pending_project_plan,
            "project_plan_path": project_plan_path,
            "project_plan_json_path": str(project_plan_json_path(state)),
            "timeline": ["detail_confirmation"],
        }

    selected_page_id = target["id"]
    page_spec = create_page_spec_from_project_plan(
        project_plan,
        selected_page_id,
        user_request=state.get("request", ""),
        existing_spec=state.get("page_spec_draft"),
    )
    page_spec = apply_page_spec_answers(page_spec, state.get("request", ""))
    missing_aspects = missing_page_spec_aspects(page_spec)
    if missing_aspects:
        clarification = _page_spec_clarification(page_spec, missing_aspects)
        return {
            "phase": "detail_confirmation",
            "status": "requires_user_input",
            "clarification": clarification,
            "detail_selection": {
                "status": "selected",
                "selected_target": target,
            },
            "selected_page_id": selected_page_id,
            "page_spec_draft": page_spec,
            "project_plan": project_plan,
            "project_plan_path": state.get("project_plan_path"),
            "project_plan_json_path": state.get("project_plan_json_path"),
            "timeline": ["detail_confirmation"],
        }

    confirmed_page_spec = page_spec
    page_detail_plan = design_page_with_main_agent(
        project_plan,
        confirmed_page_spec,
    )
    pending_project_plan = attach_page_detail_plan(project_plan, page_detail_plan)
    pending_project_plan["confirmation_status"] = "pending_user_confirmation"
    project_plan_path = write_project_plan_document(state, pending_project_plan)
    clarification = _project_plan_adjustment_confirmation_payload(pending_project_plan)

    return {
        "phase": "detail_confirmation",
        "status": "requires_user_input",
        "clarification": clarification,
        "detail_selection": {
            "status": "selected",
            "selected_target": target,
        },
        "page_spec_confirmation": {
            "status": "confirmed",
            "confirmed_page_spec": confirmed_page_spec,
            "source": "project_plan_with_user_confirmation",
        },
        "selected_page_id": selected_page_id,
        "confirmed_page_spec": confirmed_page_spec,
        "detail_plans": [page_detail_plan],
        "project_plan": project_plan,
        "pending_project_plan": pending_project_plan,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": str(project_plan_json_path(state)),
        "timeline": ["detail_confirmation"],
    }


def _detail_target_selection(project_plan: dict) -> dict:
    targets = detail_design_targets(project_plan)
    if 2 <= len(targets) <= 4:
        question = AskUserQuestion(
            header="设计对象",
            question="请选择接下来要进行详细设计的页面或数据源。",
            type="choice",
            options=[
                AskUserOption(
                    label=target["label"],
                    description=target["description"],
                )
                for target in targets
            ],
        )
    else:
        target_lines = "\n".join(
            f"- {target['label']}（id: {target['id']}）：{target['description']}"
            for target in targets
        )
        question = AskUserQuestion(
            header="设计对象",
            question=(
                "请输入要进行详细设计的页面或数据源名称/id。\n"
                f"{target_lines}"
            ),
            type="text",
            placeholder="例如：页面：库存管理列表页 或 inventory_management_list_page",
        )
    return build_ask_user_payload([question])


def _page_spec_clarification(
    page_spec: dict,
    missing_aspects: list[str],
) -> dict:
    questions = []
    if "页面目标" in missing_aspects:
        questions.append(
            AskUserQuestion(
                header="页面目标",
                question=f"{page_spec.get('page_name')} 的页面目标是什么？",
                type="text",
                placeholder="例如：帮助库管员快速查看库存状态并处理入库/出库。",
            )
        )
    if "基本布局" in missing_aspects:
        questions.append(
            AskUserQuestion(
                header="页面布局",
                question="这个页面的基本布局应包含哪些区域？",
                type="text",
                placeholder="例如：顶部筛选区、库存表格、右侧详情抽屉、批量操作栏。",
            )
        )
    if "页面交互" in missing_aspects:
        questions.append(
            AskUserQuestion(
                header="页面交互",
                question="这个页面需要支持哪些关键交互？",
                type="text",
                placeholder="例如：搜索、筛选、查看详情、提交审批、导出。",
            )
        )
    if "数据来源" in missing_aspects:
        questions.append(
            AskUserQuestion(
                header="数据来源",
                question="这个页面需要读取哪些数据源？",
                type="text",
                placeholder="请输入 project-plan 中的数据源 id 或名称。",
            )
        )
    if "页面权限" in missing_aspects:
        questions.append(
            AskUserQuestion(
                header="页面权限",
                question="哪些角色可以访问或操作这个页面？",
                type="text",
                placeholder="例如：admin、warehouse_manager、user。",
            )
        )
    if "页面依赖" in missing_aspects:
        questions.append(
            AskUserQuestion(
                header="页面依赖",
                question="这个页面依赖哪些 API 契约或其他页面状态？",
                type="text",
                placeholder="请输入 api_contract id、上游页面或关键状态。",
            )
        )
    payload = build_ask_user_payload(questions[:4])
    payload["page_spec_draft"] = page_spec
    payload["missing_aspects"] = missing_aspects
    return payload


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


def _project_plan_adjustment_confirmation_payload(project_plan: dict) -> dict:
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="计划调整",
                question=(
                    "页面/数据源详细设计已经更新项目规划书。"
                    "请确认这次 ProjectPlan 调整是否正确。"
                    "如果正确，请回复“正确，继续”；如果需要修改，请说明调整意见。"
                ),
                type="text",
                placeholder="例如：正确，继续 / 页面权限还需要增加审批人。",
            )
        ]
    )
    payload["mode"] = "project_plan_adjustment_confirmation"
    payload["message"] = "请确认 ProjectPlan 调整后再进入任务拆分和代码生成。"
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
