from app.agents.main.task_preparer import prepare_build_tasks_with_main_agent
from app.graph.nodes.common import capture_agent_file_changes, workspace_from_state
from app.graph.state import ProjectState
from app.workspace.code_changes import code_change_state_update
from app.workspace.task_documents import write_build_task_plan_json


def prepare_build_tasks(state: ProjectState) -> dict:
    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="main.prepare_build_tasks",
        action=lambda: prepare_build_tasks_with_main_agent(
            state["project_plan"],
            workspace=workspace,
        ),
    )
    build_task_plan = captured.value
    build_task_plan_path = write_build_task_plan_json(state, build_task_plan)
    return {
        **code_change_state_update(captured.code_change_set),
        "phase": "prepare_build_tasks",
        "build_task_plan": build_task_plan,
        "build_task_plan_path": build_task_plan_path,
        "tasks": build_task_plan["tasks"],
        "timeline": ["prepare_build_tasks"],
    }
