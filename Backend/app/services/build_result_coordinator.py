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
    """把专业 Agent 响应规整为构建结果记录。"""

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
    """统计任务状态和执行归属，用于构建阶段摘要。"""

    return {
        "total": len(tasks),
        "completed": len([task for task in tasks if task.get("status") == "completed"]),
        "already_satisfied": len([task for task in tasks if task.get("status") == "already_satisfied"]),
        "failed": len([task for task in tasks if task.get("status") == "failed"]),
        "pending": len([task for task in tasks if task.get("status") == "pending"]),
        "running": len([task for task in tasks if task.get("status") == "running"]),
        "frontend": len([task for task in tasks if task.get("owner") == "frontend"]),
        "data_source": len([task for task in tasks if task.get("owner") == "data_source"]),
    }


def _failure_reason_from_result(result: dict[str, Any] | None) -> str:
    """从任务结果中提取用户可读的失败原因。"""

    if not isinstance(result, dict):
        return ""
    for key in ("failure_reason", "error_message", "message", "agent_note"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    change_request = result.get("change_request")
    if isinstance(change_request, dict):
        for key in ("reason", "message", "summary"):
            value = change_request.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _failure_summary_from_result(result: dict[str, Any] | None) -> dict[str, Any]:
    """把失败分类、调度决策和说明同步到任务展示字段。"""

    if not isinstance(result, dict) or result.get("status") != "failed":
        return {
            "failure_category": None,
            "failure_reason": None,
            "failure_detail": None,
        }
    scheduler_decision = result.get("scheduler_decision")
    return {
        "failure_category": result.get("failure_category")
        or result.get("error_category")
        or result.get("category"),
        "failure_reason": _failure_reason_from_result(result),
        "failure_detail": {
            "scheduler_decision": scheduler_decision if isinstance(scheduler_decision, dict) else {},
            "changed_files": result.get("changed_files") if isinstance(result.get("changed_files"), list) else [],
        },
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
    """作为调度协调边界合并专业 Agent 的任务结果。"""

    now = datetime.now(UTC).isoformat()
    result_by_task_id = {result["task_id"]: result for result in new_results}

    updated_tasks = []
    for task in tasks:
        result = result_by_task_id.get(task["id"])
        if not result:
            updated_tasks.append(task)
            continue

        # 处理已满足状态：保留代理报告的状态
        if result.get("status") == "already_satisfied":
            status = "already_satisfied"
        elif result.get("status") == "failed":
            status = "failed"
        else:
            status = "completed"
        updated_tasks.append(
            {
                **task,
                "status": status,
                "last_result_status": result.get("status"),
                **_failure_summary_from_result(result),
                "updated_by": "build-scheduler",
                "updated_at": now,
            }
        )

    all_results = [*existing_results, *new_results]
    summary = _task_status_counts(updated_tasks)
    # 将 already_satisfied 计入 completed 用于总体统计
    summary["completed"] = summary["completed"] + summary.get("already_satisfied", 0)

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
