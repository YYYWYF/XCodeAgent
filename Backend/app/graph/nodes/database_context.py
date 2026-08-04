from app.graph.nodes.common import workspace_from_state
from app.graph.nodes.tasks import (
    _build_execution_scope_from_state,
    _latest_compact_project_plan,
    _resolve_build_context,
)
from app.graph.state import ProjectState
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.database_planning_context import (
    database_context_requirement,
    prepare_database_planning_context,
)
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.task_documents import (
    build_task_plan_json_path,
    load_build_task_plan_json,
)
from app.workspace.workspace_snapshot_documents import load_workspace_snapshot_json


def inspect_database_context(state: ProjectState) -> dict:
    """在任务规划前连接数据库、采集事实结构并编译差异任务意图。"""

    project_plan = _latest_compact_project_plan(state)
    workspace_snapshot = _workspace_snapshot_from_state(state)
    build_execution_scope = _build_execution_scope_from_state(state)
    build_task_plan = _build_task_plan_for_context(state, project_plan, workspace_snapshot)
    build_context = _resolve_build_context(
        state,
        project_plan,
        build_execution_scope,
        build_task_plan,
    )
    requirement = database_context_requirement(project_plan, build_context)
    if not requirement.get("required"):
        return {
            "phase": "inspect_database_context",
            "status": "completed",
            "project_plan": project_plan,
            "build_execution_scope": build_execution_scope,
            "build_context": build_context,
            "database_planning_context": {},
            "timeline": ["inspect_database_context"],
        }
    database_context = prepare_database_planning_context(
        project_plan,
        build_context,
        workspace_from_state(state),
    )
    if database_context.get("status") == "connection_failed":
        return _blocked_result(
            project_plan,
            build_execution_scope,
            build_context,
            {
                "reason": database_context.get("reason")
                or database_context.get("status"),
                "message": database_context.get("summary")
                or database_context.get("message")
                or "数据库摘要获取失败。",
                "targets": database_context.get("targets") or [],
            },
        )
    build_context = {
        **build_context,
        "database_planning_context": database_context,
    }
    return {
        "phase": "inspect_database_context",
        "status": "completed",
        "project_plan": project_plan,
        "build_execution_scope": build_execution_scope,
        "build_context": build_context,
        "database_planning_context": database_context,
        "timeline": ["inspect_database_context"],
    }


def _blocked_result(
    project_plan: dict,
    build_execution_scope: dict,
    build_context: dict,
    requirement: dict,
) -> dict:
    """生成数据库连接失败阻断结果；结构差异不在此处阻断。"""

    message = str(requirement.get("message") or "数据库上下文检查未通过。")
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="数据库上下文",
                question=(
                    f"{message} 请在application.json中正确配置当前应用的数据源连接后重试。"
                ),
                type="text",
                placeholder="例如：已补齐数据库连接信息，请重新检查。",
            )
        ]
    )
    payload["mode"] = "database_context_check"
    payload["message"] = message
    payload["targets"] = requirement.get("targets") or []
    return {
        "phase": "inspect_database_context",
        "status": "requires_user_input",
        "project_plan": project_plan,
        "build_execution_scope": build_execution_scope,
        "build_context": build_context,
        "database_planning_context": {
            "schema_version": "database-context.v1",
            "status": "blocked",
            "reason": requirement.get("reason"),
            "message": message,
            "connection": {"status": "failed"},
            "actual_schema": {},
            "required_schema": {},
            "gaps": [],
            "task_intents": [],
            "targets": requirement.get("targets") or [],
        },
        "clarification": payload,
        "timeline": ["inspect_database_context"],
    }


def _workspace_snapshot_from_state(state: ProjectState) -> dict:
    """从状态或快照文件读取工作区摘要，供 Unit 骨架保持同一输入。"""

    snapshot = state.get("workspace_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        return snapshot
    snapshot_path = state.get("workspace_snapshot_path")
    if snapshot_path:
        return load_workspace_snapshot_json(snapshot_path)
    workspace = workspace_from_state(state)
    return {"workspace": workspace} if workspace else {}


def _build_task_plan_for_context(
    state: ProjectState,
    project_plan: dict,
    workspace_snapshot: dict,
) -> dict:
    """复用已有有效 DAG 或生成只用于上下文解析的 Unit 骨架。"""

    in_state = state.get("build_task_plan")
    if isinstance(in_state, dict) and in_state.get("schema_version") == "build-dag.v3":
        return in_state
    plan_path = build_task_plan_json_path(state)
    if plan_path.is_file():
        persisted = load_build_task_plan_json(plan_path)
        if (
            isinstance(persisted, dict)
            and persisted.get("schema_version") == "build-dag.v3"
        ):
            return persisted
    return ensure_build_unit_skeleton(project_plan, workspace_snapshot, {})
