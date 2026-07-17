from app.agents.main.document_sync import sync_project_plan_from_markdown
from app.agents.main.planner import revise_project_plan_with_chat_model
from app.agents.main.task_preparer import prepare_build_tasks_with_main_agent
from app.graph.nodes.confirmation import (
    user_confirmed_text,
    user_requested_changes_text,
)
from app.graph.nodes.common import workspace_from_state
from app.graph.state import ProjectState
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.page_dependencies import validate_project_plan_dependencies
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.plan_documents import (
    edited_project_plan_markdown,
    project_plan_markdown_path,
    write_project_plan_document,
    write_project_plan_json,
)
from app.workspace.task_documents import (
    write_build_task_dag_markdown,
    write_build_task_plan_json,
)
from app.workspace.workspace_snapshot_documents import load_workspace_snapshot_json


def prepare_build_tasks(state: ProjectState) -> dict:
    project_plan = state["project_plan"]
    if project_plan.get("confirmation_status") != "confirmed":
        if _user_confirmed_project_plan(state.get("request", "")):
            edited_markdown = edited_project_plan_markdown(state, project_plan)
            synchronized_plan = (
                sync_project_plan_from_markdown(
                    project_plan,
                    state["requirement_spec"],
                    edited_markdown,
                )
                if edited_markdown is not None
                else project_plan
            )
            project_plan = {
                **synchronized_plan,
                "confirmation_status": "confirmed",
            }
            if project_plan_markdown_path(state).is_file():
                write_project_plan_json(state, project_plan)
            else:
                write_project_plan_document(state, project_plan)
        elif user_requested_changes_text(state.get("request", "")):
            project_plan = revise_project_plan_with_chat_model(
                project_plan,
                state.get("request", ""),
            )
            project_plan_path = write_project_plan_document(state, project_plan)
            clarification = _project_plan_confirmation_payload(project_plan)
            return {
                "phase": "prepare_build_tasks",
                "status": "requires_user_input",
                "project_plan": project_plan,
                "project_plan_path": project_plan_path,
                "clarification": clarification,
                "timeline": ["prepare_build_tasks"],
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

    contract_errors = [
        *validate_project_plan_dependencies(project_plan),
        *validate_api_contract_consistency(project_plan),
    ]
    if contract_errors:
        return {
            "phase": "prepare_build_tasks",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "clarification": _api_contract_inconsistency_payload(contract_errors),
            "timeline": ["prepare_build_tasks"],
        }

    workspace = workspace_from_state(state)
    workspace_snapshot = _workspace_snapshot_from_state(state)
    try:
        build_task_plan = prepare_build_tasks_with_main_agent(
            project_plan,
            workspace=workspace,
            workspace_snapshot=workspace_snapshot,
        )
    except ValueError as exc:
        return {
            "phase": "prepare_build_tasks",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "clarification": _build_task_plan_generation_error_payload(str(exc)),
            "timeline": ["prepare_build_tasks"],
        }
    dag_errors = (
        build_task_plan.get("dag", {})
        .get("validation", {})
        .get("errors", [])
    )
    if dag_errors:
        return {
            "phase": "prepare_build_tasks",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "build_task_plan": build_task_plan,
            "clarification": _build_task_plan_validation_error_payload(dag_errors),
            "timeline": ["prepare_build_tasks"],
        }
    build_task_plan_path = write_build_task_plan_json(state, build_task_plan)
    build_task_dag_path = write_build_task_dag_markdown(state, build_task_plan)
    return {
        "phase": "prepare_build_tasks",
        "status": "completed",
        "project_plan": project_plan,
        "build_task_plan": build_task_plan,
        "build_task_plan_path": build_task_plan_path,
        "build_task_dag_path": build_task_dag_path,
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


def _workspace_snapshot_from_state(state: ProjectState) -> dict:
    snapshot = state.get("workspace_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        return snapshot
    snapshot_path = state.get("workspace_snapshot_path")
    if snapshot_path:
        return load_workspace_snapshot_json(snapshot_path)
    return {}


def _api_contract_inconsistency_payload(errors: list[str]) -> dict:
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="契约校验",
                question=(
                    "API 契约与数据源或页面字段引用不一致，已阻止代码生成。"
                    "请返回项目规划阶段修订契约后再继续。"
                ),
                type="text",
                placeholder="例如：请按校验错误修订 API 契约和页面字段引用。",
            )
        ]
    )
    payload["mode"] = "api_contract_consistency_error"
    payload["message"] = "API 契约一致性校验失败，已阻止任务拆分和代码生成。"
    payload["errors"] = errors
    return payload


def _build_task_plan_generation_error_payload(error: str) -> dict:
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="任务拆分",
                question=(
                    "Build DAG 生成失败，模型没有返回可执行的任务列表。"
                    "请确认是否重试任务拆分，或返回项目规划阶段调整计划。"
                ),
                type="text",
                placeholder="例如：请重试任务拆分 / 返回项目规划阶段补充任务边界。",
            )
        ]
    )
    payload["mode"] = "build_task_plan_generation_error"
    payload["message"] = "Build DAG 生成失败，已阻止代码生成。"
    payload["error"] = error
    return payload


def _build_task_plan_validation_error_payload(errors: list[str]) -> dict:
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="DAG 校验",
                question=(
                    "Build DAG 校验失败，存在缺失依赖或循环依赖。"
                    "请确认是否重试任务拆分，或返回项目规划阶段调整任务边界。"
                ),
                type="text",
                placeholder="例如：请重试任务拆分 / 返回项目规划阶段补充依赖关系。",
            )
        ]
    )
    payload["mode"] = "build_task_plan_validation_error"
    payload["message"] = "Build DAG 校验失败，已阻止代码生成。"
    payload["errors"] = errors
    return payload


def _user_confirmed_project_plan(request: str) -> bool:
    return user_confirmed_text(
        request,
        positive_signals=("正确", "没问题", "继续", "可以继续", "无误"),
        negative_signals=("不正确", "需要修改", "修改", "调整", "补充", "不对"),
    )
