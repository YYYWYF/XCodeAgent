from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.services.build_task_planner import replace_build_task_plan_tasks
from app.utils.model_output import extract_json_object


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


def create_agent_task_results(
    tasks: list[dict[str, Any]],
    agent_note: str,
    executed_by: dict[str, Any] | None = None,
    *,
    require_structured: bool = False,
) -> list[dict[str, Any]]:
    """解析 Agent 的逐任务报告；严格模式拒绝缺失或损坏的结构化终态。"""

    reports, is_structured = _structured_task_reports(agent_note)
    structured_contract = is_structured or require_structured
    return [
        _task_result_from_report(
            task,
            reports.get(str(task.get("id") or "")),
            agent_note=agent_note,
            executed_by=executed_by,
            missing_structured_report=structured_contract
            and str(task.get("id") or "") not in reports,
        )
        for task in tasks
    ]


def _structured_task_reports(agent_note: str) -> tuple[dict[str, dict[str, Any]], bool]:
    """从最终 JSON 中提取以任务 ID 索引的结构化报告。"""

    payload = extract_json_object(agent_note)
    contract_marker_present = (
        '"task_results"' in agent_note or '"task_id"' in agent_note
    )
    if not isinstance(payload, dict):
        return {}, contract_marker_present
    if contract_marker_present and "task_results" not in payload and not payload.get("task_id"):
        # 顶层报告损坏时 extract_json_object 可能回退到内部 evidence 对象；
        # 此时必须保留“结构化协议已尝试但无效”的事实，禁止降级成旧版 completed。
        return {}, True
    raw_reports = payload.get("task_results")
    if not isinstance(raw_reports, list):
        raw_reports = [payload] if payload.get("task_id") else []
    return (
        {
            str(report.get("task_id")): report
            for report in raw_reports
            if isinstance(report, dict) and report.get("task_id")
        },
        "task_results" in payload or bool(payload.get("task_id")),
    )


def _task_result_from_report(
    task: dict[str, Any],
    report: dict[str, Any] | None,
    *,
    agent_note: str,
    executed_by: dict[str, Any] | None,
    missing_structured_report: bool = False,
) -> dict[str, Any]:
    """把单个结构化报告规整为调度器结果，未提供报告时保持旧版兼容。"""

    if missing_structured_report:
        report = {
            "status": "failed",
            "summary": "Agent structured response omitted this dispatched task.",
            "failure_category": "runner_protocol_error",
            "failure_reason": "Agent structured response omitted this dispatched task.",
        }
    if not isinstance(report, dict):
        return create_agent_task_result(task, agent_note, executed_by)
    status = str(report.get("status") or "").strip()
    if status not in {"completed", "already_satisfied", "failed"}:
        status = "failed"
        failure_category = "runner_protocol_error"
        failure_reason = f"Agent returned invalid task status: {report.get('status')!r}."
    else:
        failure_category = report.get("failure_category")
        failure_reason = report.get("failure_reason")
    summary = str(report.get("summary") or report.get("agent_note") or "").strip()
    return {
        "task_id": task["id"],
        "owner": task["owner"],
        "status": status,
        "changed_files": [],
        "commands": [],
        "agent_note": summary or agent_note,
        "satisfaction_evidence": report.get("satisfaction_evidence"),
        "failure_category": failure_category,
        "failure_reason": failure_reason,
        "executed_by": executed_by
        or {
            "agent": task["owner"],
            "mode": "live",
            "source": "specialist_agent",
        },
        "change_request": report.get("change_request"),
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
        "backend": len([task for task in tasks if task.get("owner") == "backend"]),
        "database": len([task for task in tasks if task.get("owner") == "database"]),
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
