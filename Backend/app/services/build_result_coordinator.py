from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.services.build_task_planner import replace_build_task_plan_tasks


def create_agent_task_result(
    task: dict[str, Any],
    agent_note: str,
    executed_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize a specialist agent response into a build result record.

    Specialist agents execute only the approved task. They do not mutate the
    project plan or task DAG directly; the scheduler coordination boundary
    consumes this structure and performs state updates.
    """

    return {
        "task_id": task["id"],
        "owner": task["owner"],
        "status": "completed",
        "changed_files": [],
        "commands": [],
        "agent_note": agent_note,
        "executed_by": executed_by
        or {
            "agent": task["owner"],
            "mode": "live",
            "source": "specialist_agent",
        },
        "change_request": None,
    }


def _task_status_counts(tasks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(tasks),
        "completed": len([task for task in tasks if task.get("status") == "completed"]),
        "failed": len([task for task in tasks if task.get("status") == "failed"]),
        "pending": len([task for task in tasks if task.get("status") == "pending"]),
        "running": len([task for task in tasks if task.get("status") == "running"]),
        "frontend": len([task for task in tasks if task.get("owner") == "frontend"]),
        "data_source": len([task for task in tasks if task.get("owner") == "data_source"]),
    }


def apply_agent_results_with_scheduler(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    existing_results: list[dict[str, Any]],
    new_results: list[dict[str, Any]],
    stage: str,
) -> dict[str, Any]:
    """Apply specialist-agent results as the scheduler coordination boundary."""

    now = datetime.now(UTC).isoformat()
    result_by_task_id = {result["task_id"]: result for result in new_results}

    updated_tasks = []
    for task in tasks:
        result = result_by_task_id.get(task["id"])
        if not result:
            updated_tasks.append(task)
            continue

        status = "failed" if result.get("status") == "failed" else "completed"
        updated_tasks.append(
            {
                **task,
                "status": status,
                "last_result_status": result.get("status"),
                "updated_by": "build-scheduler",
                "updated_at": now,
            }
        )

    all_results = [*existing_results, *new_results]
    summary = _task_status_counts(updated_tasks)

    updated_build_task_plan = replace_build_task_plan_tasks(
        deepcopy(build_task_plan),
        updated_tasks,
    )
    updated_build_task_plan["summary"] = {
        **updated_build_task_plan.get("summary", {}),
        **summary,
        "results": len(all_results),
    }
    updated_build_task_plan["last_update"] = {
        "stage": stage,
        "updated_by": "build-scheduler",
        "updated_at": now,
        "applied_result_count": len(new_results),
    }

    updated_project_plan = deepcopy(project_plan)
    updated_project_plan["build_execution"] = {
        "status": "completed"
        if summary["completed"] == summary["total"] and summary["failed"] == 0
        else "in_progress",
        "updated_by": "build-scheduler",
        "updated_at": now,
        "stage": stage,
        "summary": summary,
        "task_statuses": [
            {
                "task_id": task["id"],
                "owner": task["owner"],
                "status": task.get("status", "pending"),
            }
            for task in updated_tasks
        ],
    }

    return {
        "project_plan": updated_project_plan,
        "build_task_plan": updated_build_task_plan,
        "tasks": updated_tasks,
        "build_results": all_results,
        "build_summary": {
            "completed": summary["completed"],
            "failed": summary["failed"],
            "pending": summary["pending"],
            "results": len(all_results),
        },
    }


apply_agent_results_with_main_agent = apply_agent_results_with_scheduler
