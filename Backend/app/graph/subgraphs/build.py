from langgraph.graph import END, START, StateGraph

from app.agents.data_source.generator import generate_data_sources_with_deep_agent
from app.agents.frontend.generator import generate_frontend_with_deep_agent
from app.graph.nodes.common import capture_agent_file_changes, workspace_from_state
from app.graph.state import ProjectState
from app.services.build_result_coordinator import (
    apply_agent_results_with_main_agent,
)
from app.workspace.code_changes import code_change_state_update
from app.workspace.plan_documents import (
    project_plan_json_path,
    write_project_plan_document,
)
from app.workspace.task_documents import write_build_task_plan_json


def _completed_task_ids(tasks: list[dict]) -> set[str]:
    return {task["id"] for task in tasks if task.get("status") == "completed"}


def _ready_tasks(tasks: list[dict]) -> list[dict]:
    completed = _completed_task_ids(tasks)
    return [
        task
        for task in tasks
        if task.get("status") != "completed"
        and all(dependency in completed for dependency in task.get("dependencies", []))
    ]


def _ready_tasks_for_owner(state: ProjectState, owner: str) -> list[dict]:
    return [
        task
        for task in _ready_tasks(state.get("tasks", []))
        if task.get("owner") == owner
    ]


def _main_agent_applies_pending_results(state: ProjectState, stage: str) -> dict:
    pending_results = state.get("pending_build_results", [])
    if not pending_results:
        return {"pending_build_results": []}

    updated = apply_agent_results_with_main_agent(
        project_plan=state["project_plan"],
        build_task_plan=state["build_task_plan"],
        tasks=state.get("tasks", []),
        existing_results=state.get("build_results", []),
        new_results=pending_results,
        stage=stage,
    )
    project_plan_path = write_project_plan_document(state, updated["project_plan"])
    build_task_plan_path = write_build_task_plan_json(state, updated["build_task_plan"])

    return {
        **updated,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": str(project_plan_json_path(state)),
        "build_task_plan_path": build_task_plan_path,
        "pending_build_results": [],
    }


def select_ready_build_tasks(state: ProjectState) -> dict:
    return {
        "phase": "build_select_ready_tasks",
        "ready_tasks": _ready_tasks(state.get("tasks", [])),
        "build_events": ["select_ready_tasks"],
    }


def generate_data_sources(state: ProjectState) -> dict:
    ready_tasks = _ready_tasks_for_owner(state, "data_source")
    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="data_source.deep_agent",
        action=lambda: generate_data_sources_with_deep_agent(
            project_plan=state["project_plan"],
            build_task_plan=state["build_task_plan"],
            tasks=ready_tasks,
            workspace=workspace,
        ),
    )
    return {
        **code_change_state_update(captured.code_change_set),
        "pending_build_results": captured.value,
        "phase": "build_generate_data_sources",
        "build_events": ["generate_data_sources"],
    }


def main_update_after_data_sources(state: ProjectState) -> dict:
    result = _main_agent_applies_pending_results(state, "data_source_generation")
    return {
        **result,
        "phase": "build_main_update_after_data_sources",
        "build_events": ["main_update_after_data_sources"],
    }


def generate_frontend(state: ProjectState) -> dict:
    ready_tasks = _ready_tasks_for_owner(state, "frontend")
    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="frontend.deep_agent",
        action=lambda: generate_frontend_with_deep_agent(
            project_plan=state["project_plan"],
            build_task_plan=state["build_task_plan"],
            tasks=ready_tasks,
            workspace=workspace,
        ),
    )
    return {
        **code_change_state_update(captured.code_change_set),
        "pending_build_results": captured.value,
        "phase": "build_generate_frontend",
        "build_events": ["generate_frontend"],
    }


def main_update_after_frontend(state: ProjectState) -> dict:
    result = _main_agent_applies_pending_results(state, "frontend_generation")
    return {
        **result,
        "phase": "build_main_update_after_frontend",
        "build_events": ["main_update_after_frontend"],
    }


def collect_build_results(state: ProjectState) -> dict:
    failed_tasks = [
        task for task in state.get("tasks", []) if task.get("status") == "failed"
    ]
    completed_tasks = [
        task for task in state.get("tasks", []) if task.get("status") == "completed"
    ]
    return {
        "phase": "build",
        "build_summary": {
            "completed": len(completed_tasks),
            "failed": len(failed_tasks),
            "pending": len(
                [
                    task
                    for task in state.get("tasks", [])
                    if task.get("status") == "pending"
                ]
            ),
            "results": len(state.get("build_results", [])),
        },
        "build_events": ["collect_results"],
    }


def build_build_subgraph():
    builder = StateGraph(ProjectState)

    builder.add_node("select_ready_build_tasks", select_ready_build_tasks)
    builder.add_node("generate_data_sources", generate_data_sources)
    builder.add_node("main_update_after_data_sources", main_update_after_data_sources)
    builder.add_node("generate_frontend", generate_frontend)
    builder.add_node("main_update_after_frontend", main_update_after_frontend)
    builder.add_node("collect_build_results", collect_build_results)

    builder.add_edge(START, "select_ready_build_tasks")
    builder.add_edge("select_ready_build_tasks", "generate_data_sources")
    builder.add_edge("generate_data_sources", "main_update_after_data_sources")
    builder.add_edge("main_update_after_data_sources", "generate_frontend")
    builder.add_edge("generate_frontend", "main_update_after_frontend")
    builder.add_edge("main_update_after_frontend", "collect_build_results")
    builder.add_edge("collect_build_results", END)

    return builder.compile()


_build_subgraph = build_build_subgraph()


def build(state: ProjectState) -> dict:
    result = _build_subgraph.invoke(
        {
            **state,
            "build_events": [],
            "pending_build_results": [],
            "code_changes": {},
            "code_change_sets": [],
            "timeline": [],
        }
    )
    return {
        "phase": "build",
        "project_plan": result.get("project_plan", state.get("project_plan", {})),
        "project_plan_path": result.get(
            "project_plan_path", state.get("project_plan_path")
        ),
        "project_plan_json_path": result.get(
            "project_plan_json_path", state.get("project_plan_json_path")
        ),
        "build_task_plan": result.get(
            "build_task_plan", state.get("build_task_plan", {})
        ),
        "build_task_plan_path": result.get(
            "build_task_plan_path", state.get("build_task_plan_path")
        ),
        "tasks": result.get("tasks", []),
        "ready_tasks": result.get("ready_tasks", []),
        "build_results": result.get("build_results", []),
        "build_summary": result.get("build_summary", {}),
        "build_events": result.get("build_events", []),
        "code_changes": result.get("code_changes", {}),
        "code_change_sets": result.get("code_change_sets", []),
        "timeline": ["build"],
    }
