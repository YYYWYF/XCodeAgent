from __future__ import annotations

from typing import Any, Callable

from app.agents.data_source.generator import generate_data_sources_with_deep_agent
from app.agents.frontend.generator import generate_frontend_with_deep_agent
from app.agents.repair_planner import (
    plan_build_failure_repair_with_repair_planner_agent,
)
from app.graph.nodes.common import capture_agent_file_changes, workspace_from_state
from app.graph.state import ProjectState
from app.services.build_repair_planner import (
    append_repair_tasks_to_build_plan,
    close_repaired_parent_tasks,
    create_build_failure_repair_plan,
)
from app.services.build_result_coordinator import apply_agent_results_with_scheduler
from app.services.build_scheduler import (
    mark_tasks_running,
    normalize_task_results,
    select_ready_build_batch,
    summarize_build_runtime,
)
from app.workspace.code_changes import code_change_state_update, merge_code_change_sets
from app.workspace.plan_documents import (
    project_plan_json_path,
    write_project_plan_document,
)
from app.workspace.task_documents import (
    write_build_task_dag_markdown,
    write_build_task_plan_json,
)
from app.workspace.task_documents import write_repair_task_plan_json


Runner = Callable[..., list[dict[str, Any]]]


def _runner_for_owner(owner: str) -> tuple[str, Runner] | None:
    if owner == "data_source":
        return "data_source.deep_agent", generate_data_sources_with_deep_agent
    if owner == "frontend":
        return "frontend.deep_agent", generate_frontend_with_deep_agent
    return None


def _group_tasks_by_owner(tasks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        groups.setdefault(str(task.get("owner") or ""), []).append(task)
    return groups


def _execute_ready_tasks(
    state: ProjectState,
    ready_tasks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    workspace = workspace_from_state(state)
    all_results: list[dict[str, Any]] = []
    code_change_sets: list[dict[str, Any]] = []
    for owner, owner_tasks in _group_tasks_by_owner(ready_tasks).items():
        runner_entry = _runner_for_owner(owner)
        if runner_entry is None:
            all_results.extend(
                normalize_task_results(
                    dispatched_tasks=owner_tasks,
                    raw_results=[
                        {
                            "task_id": task["id"],
                            "owner": owner,
                            "status": "failed",
                            "failure_category": "runner_protocol_error",
                            "agent_note": f"No CodeRunner is registered for owner: {owner}.",
                        }
                        for task in owner_tasks
                    ],
                )
            )
            continue

        source_tool, runner = runner_entry
        captured = capture_agent_file_changes(
            workspace=workspace,
            source_tool=source_tool,
            action=lambda owner_tasks=owner_tasks, runner=runner: runner(
                project_plan=state["project_plan"],
                build_task_plan=state["build_task_plan"],
                tasks=owner_tasks,
                workspace=workspace,
            ),
        )
        if captured.code_change_set:
            code_change_sets.append(captured.code_change_set)
        all_results.extend(
            normalize_task_results(
                dispatched_tasks=owner_tasks,
                raw_results=captured.value,
            )
        )
    return all_results, code_change_sets


def _plan_build_repair_with_repair_planner(
    state: ProjectState,
    repair_input: dict[str, Any],
) -> dict[str, Any]:
    return plan_build_failure_repair_with_repair_planner_agent(
        repair_input=repair_input,
        workspace=workspace_from_state(state),
    )


def _apply_scheduler_results(
    state: ProjectState,
    *,
    tasks: list[dict[str, Any]],
    results: list[dict[str, Any]],
    stage: str,
) -> dict[str, Any]:
    updated = apply_agent_results_with_scheduler(
        project_plan=state["project_plan"],
        build_task_plan={**state["build_task_plan"], "tasks": tasks},
        tasks=tasks,
        existing_results=state.get("build_results", []),
        new_results=results,
        stage=stage,
    )
    repaired_tasks = close_repaired_parent_tasks(
        tasks=updated["tasks"],
        results=updated.get("build_results", []),
    )
    if repaired_tasks != updated["tasks"]:
        updated["tasks"] = repaired_tasks
        updated["build_task_plan"] = {
            **updated["build_task_plan"],
            "tasks": repaired_tasks,
        }
        updated["build_summary"] = summarize_build_runtime(
            repaired_tasks,
            updated.get("build_results", []),
        )
    project_plan_path = write_project_plan_document(state, updated["project_plan"])
    build_task_plan_path = write_build_task_plan_json(state, updated["build_task_plan"])
    build_task_dag_path = write_build_task_dag_markdown(state, updated["build_task_plan"])
    return {
        **updated,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": str(project_plan_json_path(state)),
        "build_task_plan_path": build_task_plan_path,
        "build_task_dag_path": build_task_dag_path,
    }


def run_build_scheduler(state: ProjectState) -> dict[str, Any]:
    build_task_plan = dict(state.get("build_task_plan") or {})
    incoming_repair_task_plan = state.get("repair_task_plan")
    if (
        isinstance(incoming_repair_task_plan, dict)
        and incoming_repair_task_plan.get("tasks")
        and incoming_repair_task_plan.get("decision", "repair") == "repair"
    ):
        build_task_plan = append_repair_tasks_to_build_plan(
            build_task_plan={
                **build_task_plan,
                "tasks": list(state.get("tasks") or build_task_plan.get("tasks") or []),
            },
            repair_task_plan=incoming_repair_task_plan,
        )

    tasks = list(state.get("tasks") or build_task_plan.get("tasks") or [])
    if build_task_plan.get("tasks"):
        tasks = list(build_task_plan["tasks"])
    if not tasks:
        return {
            "phase": "build",
            "ready_tasks": [],
            "build_summary": {
                "total": 0,
                "completed": 0,
                "failed": 0,
                "pending": 0,
                "results": len(state.get("build_results", [])),
                "status": "completed",
            },
            "build_events": ["scheduler:no_tasks"],
        }

    current_state: ProjectState = {
        **state,
        "tasks": tasks,
        "build_task_plan": {
            **build_task_plan,
            "tasks": tasks,
        },
        "build_results": list(state.get("build_results", [])),
    }
    build_events: list[str] = []
    all_code_change_sets: list[dict[str, Any]] = []
    repair_task_plan: dict[str, Any] = state.get("repair_task_plan", {})
    repair_task_plan_path = state.get("repair_task_plan_path")
    max_iterations = max(len(tasks) * 2, 1)

    for iteration in range(1, max_iterations + 1):
        selection = select_ready_build_batch(current_state["tasks"])
        if selection["errors"]:
            build_events.append("scheduler:invalid_dag")
            break
        if selection["is_complete"]:
            build_events.append("scheduler:completed")
            break
        ready_tasks = selection["ready_tasks"]
        if not ready_tasks:
            build_events.append("scheduler:blocked")
            break

        ready_ids = selection["ready_task_ids"]
        build_events.append(f"scheduler:dispatch:{','.join(ready_ids)}")
        running_tasks = mark_tasks_running(current_state["tasks"], ready_ids)
        results, code_change_sets = _execute_ready_tasks(
            {**current_state, "tasks": running_tasks},
            ready_tasks,
        )
        all_code_change_sets.extend(code_change_sets)
        updated = _apply_scheduler_results(
            {**current_state, "tasks": running_tasks},
            tasks=running_tasks,
            results=results,
            stage=f"build_scheduler_iteration_{iteration}",
        )
        current_state = {**current_state, **updated}
        build_events.append(f"scheduler:results:{len(results)}")

        summary = summarize_build_runtime(
            current_state["tasks"],
            current_state.get("build_results", []),
        )
        if summary["status"] == "needs_repair":
            repair_task_plan = create_build_failure_repair_plan(
                failed_results=[
                    result
                    for result in current_state.get("build_results", [])
                    if result.get("status") == "failed"
                ],
                tasks=current_state["tasks"],
                existing_repair_tasks=[
                    task for task in current_state["tasks"] if task.get("kind") == "repair"
                ],
                workspace_snapshot=current_state.get("workspace_snapshot"),
                repair_planner=lambda repair_input: _plan_build_repair_with_repair_planner(
                    current_state,
                    repair_input,
                ),
            )
            repair_task_plan_path = write_repair_task_plan_json(
                current_state,
                repair_task_plan,
            )
            if repair_task_plan.get("decision") == "requires_user_confirmation":
                current_state = {
                    **current_state,
                    "repair_task_plan": repair_task_plan,
                    "repair_task_plan_path": repair_task_plan_path,
                }
                build_events.append("scheduler:repair_requires_confirmation")
                break
            if repair_task_plan.get("decision") == "terminal_failure":
                current_state = {
                    **current_state,
                    "repair_task_plan": repair_task_plan,
                    "repair_task_plan_path": repair_task_plan_path,
                }
                build_events.append("scheduler:repair_terminal_failure")
                break
            if not repair_task_plan.get("tasks"):
                build_events.append("scheduler:repair_unavailable")
                break
            next_build_task_plan = append_repair_tasks_to_build_plan(
                build_task_plan=current_state["build_task_plan"],
                repair_task_plan=repair_task_plan,
            )
            current_state = {
                **current_state,
                "build_task_plan": next_build_task_plan,
                "tasks": next_build_task_plan["tasks"],
                "repair_task_plan": repair_task_plan,
                "repair_task_plan_path": repair_task_plan_path,
                "repair_tasks": repair_task_plan["tasks"],
            }
            build_task_plan_path = write_build_task_plan_json(
                current_state,
                next_build_task_plan,
            )
            build_task_dag_path = write_build_task_dag_markdown(
                current_state,
                next_build_task_plan,
            )
            current_state["build_task_plan_path"] = build_task_plan_path
            current_state["build_task_dag_path"] = build_task_dag_path
            build_events.append(f"scheduler:repair_planned:{len(repair_task_plan['tasks'])}")
            continue

        if summary["status"] in {"requires_confirmation", "failed"}:
            build_events.append(f"scheduler:{summary['status']}")
            break
    else:
        build_events.append("scheduler:iteration_budget_exhausted")

    build_results = current_state.get("build_results", [])
    build_summary = summarize_build_runtime(current_state["tasks"], build_results)
    merged_code_changes = merge_code_change_sets(all_code_change_sets)

    return {
        "phase": "build",
        "project_plan": current_state.get("project_plan", state.get("project_plan", {})),
        "project_plan_path": current_state.get(
            "project_plan_path", state.get("project_plan_path")
        ),
        "project_plan_json_path": current_state.get(
            "project_plan_json_path", state.get("project_plan_json_path")
        ),
        "build_task_plan": current_state.get(
            "build_task_plan", state.get("build_task_plan", {})
        ),
        "build_task_plan_path": current_state.get(
            "build_task_plan_path", state.get("build_task_plan_path")
        ),
        "build_task_dag_path": current_state.get(
            "build_task_dag_path", state.get("build_task_dag_path")
        ),
        "tasks": current_state["tasks"],
        "ready_tasks": [],
        "build_results": build_results,
        "build_summary": build_summary,
        "repair_task_plan": repair_task_plan,
        "repair_task_plan_path": repair_task_plan_path,
        "repair_tasks": repair_task_plan.get("tasks", []) if isinstance(repair_task_plan, dict) else [],
        "build_events": build_events,
        **code_change_state_update(merged_code_changes),
        "timeline": ["build"],
    }


def build(state: ProjectState) -> dict:
    return run_build_scheduler(state)
