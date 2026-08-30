from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.services.build_task_planner import replace_build_task_plan_tasks
from app.utils.model_output import (
    extract_json_object,
    repair_unescaped_json_string_quotes,
)


_STRICT_REPORT_FIELDS = {
    "task_id",
    "status",
    "summary",
    "satisfaction_evidence",
    "failure_category",
    "failure_reason",
    "change_request",
}
_CHANGE_REQUEST_FAILURES = {"contract_mismatch", "plan_mismatch"}


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
    strict_schema: bool = False,
) -> list[dict[str, Any]]:
    """解析 Agent 的逐任务报告；严格模式拒绝缺失或损坏的结构化终态。"""

    reports, is_structured, parse_error, recovered = _structured_task_reports(
        agent_note,
        expected_task_ids=[str(task.get("id") or "") for task in tasks],
        strict_schema=strict_schema,
    )
    structured_contract = is_structured or require_structured
    structured_error = parse_error or (
        "Agent did not return the required structured task_results JSON."
        if require_structured and not is_structured
        else ""
    )
    return [
        _task_result_from_report(
            task,
            reports.get(str(task.get("id") or "")),
            agent_note=agent_note,
            executed_by=executed_by,
            missing_structured_report=structured_contract
            and str(task.get("id") or "") not in reports,
            structured_error=structured_error,
            structured_response_recovered=recovered,
        )
        for task in tasks
    ]


def _structured_task_reports(
    agent_note: str,
    *,
    expected_task_ids: list[str] | None = None,
    strict_schema: bool = False,
) -> tuple[dict[str, dict[str, Any]], bool, str, bool]:
    """从最终 JSON 中提取以任务 ID 索引的结构化报告。"""

    payload = extract_json_object(agent_note)
    contract_marker_present = (
        '"task_results"' in agent_note or '"task_id"' in agent_note
    )
    recovered = False
    if contract_marker_present and not _is_task_report_payload(payload):
        repaired_note = repair_unescaped_json_string_quotes(agent_note)
        if repaired_note != agent_note:
            repaired_payload = extract_json_object(repaired_note)
            if _is_task_report_payload(repaired_payload):
                payload = repaired_payload
                recovered = True
    if not isinstance(payload, dict):
        return (
            {},
            contract_marker_present,
            (
                "Agent returned malformed structured task_results JSON."
                if contract_marker_present
                else ""
            ),
            False,
        )
    if contract_marker_present and "task_results" not in payload and not payload.get("task_id"):
        # 顶层报告损坏时 extract_json_object 可能回退到内部 evidence 对象；
        # 此时必须保留“结构化协议已尝试但无效”的事实，禁止降级成旧版 completed。
        return {}, True, "Agent returned an invalid structured task_results object.", recovered
    raw_reports = payload.get("task_results")
    if not isinstance(raw_reports, list):
        raw_reports = [payload] if payload.get("task_id") else []
    if strict_schema:
        schema_error = _strict_task_report_error(
            payload,
            raw_reports,
            expected_task_ids or [],
        )
        if schema_error:
            return {}, True, schema_error, recovered
    return (
        {
            str(report.get("task_id")): report
            for report in raw_reports
            if isinstance(report, dict) and report.get("task_id")
        },
        "task_results" in payload or bool(payload.get("task_id")),
        "",
        recovered,
    )


def _strict_task_report_error(
    payload: dict[str, Any],
    raw_reports: list[Any],
    expected_task_ids: list[str],
) -> str:
    """校验 Java Agent 的唯一顶层结构、任务集合和条件结果字段。"""

    if set(payload) != {"task_results"} or not isinstance(payload.get("task_results"), list):
        return "Agent task result must contain only the top-level task_results array."
    if any(not isinstance(report, dict) for report in raw_reports):
        return "Agent task_results must contain only JSON objects."

    reports = [report for report in raw_reports if isinstance(report, dict)]
    report_ids = [str(report.get("task_id") or "") for report in reports]
    if any(not task_id for task_id in report_ids):
        return "Agent task_results contains a result without task_id."
    if len(report_ids) != len(set(report_ids)):
        return "Agent task_results contains duplicate task_id values."

    expected = set(expected_task_ids)
    actual = set(report_ids)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        return f"Agent task_results contains unknown task_id values: {', '.join(unknown)}."
    if missing:
        return f"Agent task_results omitted task_id values: {', '.join(missing)}."

    for report in reports:
        task_id = str(report.get("task_id") or "")
        extra_fields = sorted(set(report) - _STRICT_REPORT_FIELDS)
        if extra_fields:
            return (
                f"Agent result {task_id} contains unsupported fields: "
                f"{', '.join(extra_fields)}."
            )
        result_error = _strict_result_condition_error(report)
        if result_error:
            return f"Agent result {task_id} {result_error}"
    return ""


def _strict_result_condition_error(report: dict[str, Any]) -> str:
    """按任务状态校验摘要、满足证据、失败原因和 change_request 条件。"""

    status = str(report.get("status") or "").strip()
    if status not in {"completed", "already_satisfied", "failed"}:
        return f"has invalid status {report.get('status')!r}."
    if not str(report.get("summary") or "").strip():
        return "must include a non-empty summary."

    failure_fields = {"failure_category", "failure_reason"}
    if status != "failed" and any(field in report for field in failure_fields):
        return "must not include failure fields unless status is failed."
    if status != "already_satisfied" and "satisfaction_evidence" in report:
        return "must not include satisfaction_evidence for this status."
    # 已满足状态的可信证据只能由调度器基于当前磁盘与工程检查生成；
    # Agent 的自报内容不能作为完成依据，因此这里不把它设为前置条件。

    if status != "failed":
        if "change_request" in report:
            return "must not include change_request unless status is failed."
        return ""

    category = str(report.get("failure_category") or "").strip()
    if not category or not str(report.get("failure_reason") or "").strip():
        return "must include failure_category and failure_reason."
    has_change_request = bool(report.get("change_request"))
    if category in _CHANGE_REQUEST_FAILURES and not has_change_request:
        return "must include a non-empty change_request for contract or plan mismatch."
    if category not in _CHANGE_REQUEST_FAILURES and "change_request" in report:
        return "must not include change_request for this failure category."
    return ""


def _is_task_report_payload(payload: Any) -> bool:
    """判断解析结果是否为任务报告顶层对象，避免把内部 evidence 误当成完整响应。"""

    return isinstance(payload, dict) and (
        "task_results" in payload or bool(payload.get("task_id"))
    )


def _task_result_from_report(
    task: dict[str, Any],
    report: dict[str, Any] | None,
    *,
    agent_note: str,
    executed_by: dict[str, Any] | None,
    missing_structured_report: bool = False,
    structured_error: str = "",
    structured_response_recovered: bool = False,
) -> dict[str, Any]:
    """把单个结构化报告规整为调度器结果，未提供报告时保持旧版兼容。"""

    if missing_structured_report:
        failure_reason = structured_error or "Agent structured response omitted this dispatched task."
        report = {
            "status": "failed",
            "summary": failure_reason,
            "failure_category": (
                "invalid_structured_response"
                if structured_error
                else "runner_protocol_error"
            ),
            "failure_reason": failure_reason,
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
        "structured_response_recovered": structured_response_recovered,
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
    """把失败分类、调度决策和两类验收证据同步到任务展示字段。"""

    if not isinstance(result, dict):
        return {
            "failure_category": None,
            "failure_reason": None,
            "failure_detail": None,
            "acceptance_status": {},
            "acceptance_evidence": [],
            "business_acceptance_evidence": [],
            "business_acceptance_summary": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "blocked": 0,
                "duration_ms_total": 0,
                "duration_ms_avg": 0,
                "by_kind": {},
            },
        }
    scheduler_decision = result.get("scheduler_decision")
    failure_fields = {
        "failure_category": result.get("failure_category")
        or result.get("error_category")
        or result.get("category"),
        "failure_reason": _failure_reason_from_result(result)
        if result.get("status") == "failed"
        else None,
        "failure_detail": {
            "scheduler_decision": scheduler_decision if isinstance(scheduler_decision, dict) else {},
            "changed_files": result.get("changed_files") if isinstance(result.get("changed_files"), list) else [],
        }
        if result.get("status") == "failed"
        else None,
        "acceptance_status": result.get("acceptance_status")
        if isinstance(result.get("acceptance_status"), dict)
        else {},
        "acceptance_evidence": result.get("acceptance_evidence")
        if isinstance(result.get("acceptance_evidence"), list)
        else [],
        "business_acceptance_evidence": result.get("business_acceptance_evidence")
        if isinstance(result.get("business_acceptance_evidence"), list)
        else [],
        "business_acceptance_summary": result.get("business_acceptance_summary")
        if isinstance(result.get("business_acceptance_summary"), dict)
        else {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "blocked": 0,
            "duration_ms_total": 0,
            "duration_ms_avg": 0,
            "by_kind": {},
        },
    }
    return failure_fields


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
