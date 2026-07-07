from app.agents.main.task_preparer import prepare_build_tasks_with_main_agent
from app.graph.state import ProjectState
from app.workspace.task_documents import write_build_task_plan_json


def prepare_build_tasks(state: ProjectState) -> dict:
    build_task_plan = prepare_build_tasks_with_main_agent(state["project_plan"])
    build_task_plan_path = write_build_task_plan_json(state, build_task_plan)
    return {
        "phase": "prepare_build_tasks",
        "build_task_plan": build_task_plan,
        "build_task_plan_path": build_task_plan_path,
        "tasks": build_task_plan["tasks"],
        "timeline": ["prepare_build_tasks"],
    }
