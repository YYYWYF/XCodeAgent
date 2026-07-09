from app.agents.main.task_preparer import prepare_build_tasks_with_main_agent
from app.graph.nodes.confirmation import user_confirmed_text
from app.graph.nodes.common import capture_agent_file_changes, workspace_from_state
from app.graph.state import ProjectState
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.code_changes import code_change_state_update
from app.workspace.task_documents import write_build_task_plan_json


def prepare_build_tasks(state: ProjectState) -> dict:
    project_plan = state["project_plan"]
    if project_plan.get("confirmation_status") != "confirmed":
        if _user_confirmed_project_plan(state.get("request", "")):
            project_plan = {
                **project_plan,
                "confirmation_status": "confirmed",
            }
        else:
            clarification = _project_plan_confirmation_payload(project_plan)
            return {
                "phase": "prepare_build_tasks",
                "status": "requires_user_input",
                "project_plan": project_plan,
                "clarification": clarification,
                "timeline": ["prepare_build_tasks"],
            }

    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="main.prepare_build_tasks",
        action=lambda: prepare_build_tasks_with_main_agent(
            project_plan,
            workspace=workspace,
        ),
    )
    build_task_plan = captured.value
    build_task_plan_path = write_build_task_plan_json(state, build_task_plan)
    return {
        **code_change_state_update(captured.code_change_set),
        "phase": "prepare_build_tasks",
        "status": "completed",
        "project_plan": project_plan,
        "build_task_plan": build_task_plan,
        "build_task_plan_path": build_task_plan_path,
        "tasks": build_task_plan["tasks"],
        "timeline": ["prepare_build_tasks"],
    }


def _project_plan_confirmation_payload(project_plan: dict) -> dict:
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="计划确认",
                question=(
                    "代码生成即将开始，但当前 ProjectPlan 尚未由用户确认。"
                    "请确认项目规划书是否正确。正确请回复“正确，继续”；"
                    "如需调整，请说明要修改的架构、API、页面、数据源、权限或验收标准。"
                ),
                type="text",
                placeholder="例如：正确，继续 / 需要增加审批流 API。",
            )
        ]
    )
    payload["mode"] = "project_plan_confirmation"
    payload["message"] = "ProjectPlan 未确认，已阻止任务拆分和代码生成。"
    payload["plan_summary"] = project_plan.get("app", {}).get("name", "未命名应用")
    return payload


def _user_confirmed_project_plan(request: str) -> bool:
    return user_confirmed_text(
        request,
        positive_signals=("正确", "没问题", "继续", "可以继续", "无误"),
        negative_signals=("不正确", "需要修改", "修改", "调整", "补充", "不对"),
    )
