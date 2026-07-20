"""将主工作流内部状态映射为稳定且可安全发送给前端的数据。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.protocols.workflow.definition import (
    WORKFLOW_ARTIFACT_FIELDS,
    WORKFLOW_EVENT_PROTOCOL,
    WORKFLOW_NODE_LABELS,
    WORKFLOW_STATIC_NEXT_NODES,
)
from app.workspace.code_changes import merge_code_change_sets


def _workflow_progress_summary(
    result: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    last_event = events[-1] if events else {}
    completed_nodes = [
        event for event in events if event.get("type") == "workflow.node.completed"
    ]
    failed_events = [event for event in events if str(event.get("status")) == "failed"]
    node = last_event.get("node") if isinstance(last_event.get("node"), dict) else {}
    code_changes = _workflow_code_changes(result)

    return {
        "status": last_event.get("status") or result.get("status") or "running",
        "phase": result.get("phase") or node.get("id"),
        "message": last_event.get("message") or "Workflow is running.",
        "completedNodeCount": len(completed_nodes),
        "failedEventCount": len(failed_events),
        "timeline": result.get("timeline", []),
        "qualityGatePassed": result.get("quality_gate_passed"),
        "needsRevision": result.get("needs_revision"),
        "previewUrl": result.get("preview_url"),
        "buildSummary": result.get("build_summary", {}),
        "testSummary": {},
        "codeChangesSummary": code_changes.get("summary") if code_changes else None,
        "artifacts": _workflow_artifacts(result),
        "clarification": result.get("clarification", {}),
    }


def _workflow_node_label(node_name: str) -> str:
    return WORKFLOW_NODE_LABELS.get(node_name, node_name)


def _workflow_start_node(
    resume_from: str | None,
    workflow_scope: str | None = None,
) -> str:
    """返回页面细节确认或其后的主 Workflow 展示入口。"""

    if workflow_scope == "application_planning":
        return (
            resume_from
            if resume_from in {"requirements", "project_planning"}
            else "requirements"
        )
    supported = set(WORKFLOW_NODE_LABELS) - {"handle_failure"}
    return resume_from if resume_from in supported else "detail_confirmation"


def _workflow_next_nodes(node_name: str, update: dict[str, Any]) -> list[str]:
    """仅预测下一个 UI 时间线节点，不参与 LangGraph 实际路由。"""

    if node_name == "integration_test":
        return (
            ["launch_project"]
            if update.get("quality_gate_passed")
            else ["handle_failure"]
        )
    if node_name == "detail_confirmation":
        if update.get("status") == "requires_user_input":
            return []
        if update.get("workflow_scope") == "application_planning":
            return []
        return ["inspect_workspace"]
    if node_name == "inspect_workspace":
        return ["prepare_build_tasks"]
    if node_name == "prepare_build_tasks":
        if update.get("status") == "requires_user_input":
            return []
        return ["build"]
    return WORKFLOW_STATIC_NEXT_NODES.get(node_name, [])


def _workflow_artifacts(value: dict[str, Any]) -> dict[str, Any]:
    return {
        field: value.get(field)
        for field in WORKFLOW_ARTIFACT_FIELDS
        if value.get(field) and not str(value.get(field)).lower().endswith(".json")
    }


def _public_workflow_state(value: dict[str, Any]) -> dict[str, Any]:
    """在状态发送到前端前移除内部 JSON 工件路径。"""

    return {
        key: item
        for key, item in value.items()
        if key not in {"requirement_spec_json_path", "project_plan_json_path"}
        and not (key.endswith("_path") and str(item).lower().endswith(".json"))
    }


def _workflow_node_detail(node_name: str, update: dict[str, Any]) -> dict[str, Any]:
    if node_name == "classify_request_complexity":
        return {
            "message": f"复杂度={update.get('request_complexity')}，原因={update.get('complexity_reason')}",
            "data": {
                "requestComplexity": update.get("request_complexity"),
                "complexityDecision": update.get("complexity_decision"),
            },
        }
    if node_name == "requirements":
        clarification = update.get("clarification")
        questions = (
            clarification.get("questions", [])
            if isinstance(clarification, dict)
            and isinstance(clarification.get("questions"), list)
            else []
        )
        status = (
            clarification.get("status")
            if isinstance(clarification, dict)
            else None
        )
        message = f"需求文档={update.get('requirement_spec_path')}"
        if questions:
            message += f"，待确认问题={len(questions)}"
        return {
            "message": message,
            "data": {
                "clarification": clarification,
                "requiresUserInput": status == "requires_user_input",
            },
        }
    if node_name == "project_planning":
        clarification = update.get("clarification")
        status = update.get("status")
        if status == "requires_user_input":
            questions = (
                clarification.get("questions", [])
                if isinstance(clarification, dict)
                and isinstance(clarification.get("questions"), list)
                else []
            )
            return {
                "message": (
                    f"计划文档={update.get('project_plan_path')}，"
                    f"待确认问题={len(questions)}"
                ),
                "data": {
                    "projectPlan": update.get("project_plan"),
                    "clarification": clarification,
                    "requiresUserInput": True,
                },
            }
        return {
            "message": (
                f"计划文档={update.get('project_plan_path')}"
            ),
            "data": {"projectPlan": update.get("project_plan")},
        }
    if node_name == "detail_confirmation":
        clarification = update.get("clarification")
        status = update.get("status")
        if status == "requires_user_input":
            review = (
                clarification.get("review", {})
                if isinstance(clarification, dict)
                else {}
            )
            summary = review.get("summary", {}) if isinstance(review, dict) else {}
            return {
                "message": (
                    "页面/数据源初版设计待整体确认，"
                    f"页面={summary.get('page_count', 0)}，"
                    f"数据源={summary.get('data_source_count', 0)}"
                ),
                "data": {
                    "clarification": clarification,
                    "requiresUserInput": True,
                    "detailSelection": update.get("detail_selection"),
                },
            }
        return {
            "message": _detail_confirmation_completed_message(update),
            "data": {
                "detailSelection": update.get("detail_selection"),
                "detailPlans": update.get("detail_plans", []),
            },
        }
    if node_name == "prepare_build_tasks":
        clarification = update.get("clarification")
        if update.get("status") == "requires_user_input":
            questions = (
                clarification.get("questions", [])
                if isinstance(clarification, dict)
                and isinstance(clarification.get("questions"), list)
                else []
            )
            return {
                "message": f"项目计划未确认，已阻止代码生成，待确认问题={len(questions)}",
                "data": {
                    "projectPlan": update.get("project_plan"),
                    "clarification": clarification,
                    "requiresUserInput": True,
                },
            }
        tasks = update.get("tasks") if isinstance(update.get("tasks"), list) else []
        return {
            "message": f"任务数={len(tasks)}，任务 DAG 已生成",
            "data": {
                "buildTaskPlan": update.get("build_task_plan"),
                "taskCount": len(tasks),
            },
        }
    if node_name == "build":
        summary = update.get("build_summary", {})
        return {
            "message": f"完成={summary.get('completed', 0)}，失败={summary.get('failed', 0)}",
            "data": {
                "buildSummary": summary,
                "buildEvents": update.get("build_events", []),
                "buildResults": update.get("build_results", []),
            },
        }
    if node_name == "integration_test":
        report = update.get("test_report", {})
        summary = report.get("summary", {}) if isinstance(report, dict) else {}
        report_path = update.get("test_report_path")
        report_suffix = (
            f"，报告={report_path}"
            if report_path and not str(report_path).lower().endswith(".json")
            else ""
        )
        return {
            "message": (
                f"通过={report.get('passed') if isinstance(report, dict) else None}，"
                f"检查={summary.get('passed', 0)}/{summary.get('total', 0)}"
                f"{report_suffix}"
            ),
            "data": {
                "testReport": report,
                "testEvents": update.get("test_events", []),
                "qualityGatePassed": update.get("quality_gate_passed"),
                "needsRevision": update.get("needs_revision"),
                "revisionRequests": update.get("revision_requests", []),
                "repairTaskPlan": update.get("repair_task_plan"),
                "integrationNextAction": update.get("integration_next_action"),
                "repairIteration": update.get("repair_iteration"),
                "maxRepairIterations": update.get("max_repair_iterations"),
            },
        }
    if node_name == "launch_project":
        return {
            "message": f"预览地址={update.get('preview_url')}",
            "data": {
                "previewUrl": update.get("preview_url"),
                "acceptanceRequest": update.get("acceptance_request"),
                "launchResult": update.get("launch_result"),
            },
        }
    if node_name == "acceptance":
        return {
            "message": f"验收={update.get('accepted')}",
            "data": {"accepted": update.get("accepted")},
        }
    if node_name in {"finalize_project", "handle_failure"}:
        return {
            "message": f"状态={update.get('status')}",
            "data": {"status": update.get("status"), "phase": update.get("phase")},
        }
    return {"message": "", "data": {}}


def _detail_confirmation_completed_message(update: dict[str, Any]) -> str:
    detail_selection = update.get("detail_selection")
    summary = {}
    if isinstance(detail_selection, dict) and isinstance(
        detail_selection.get("summary"),
        dict,
    ):
        summary = detail_selection["summary"]
    project_plan = update.get("project_plan")
    if not summary and isinstance(project_plan, dict):
        candidate = project_plan.get("detail_confirmation_summary")
        if isinstance(candidate, dict):
            summary = candidate

    if summary.get("all_detail_targets_completed"):
        return (
            "页面/数据源详细设计已全部完成，最终项目计划书已更新，"
            "准备进入任务拆分。"
        )
    remaining = summary.get("remaining_total")
    if isinstance(remaining, int):
        return f"项目计划书已更新，剩余待设计对象={remaining}"
    return "项目计划书已更新"


def _workflow_event(
    events: list[dict[str, Any]],
    event_type: str,
    *,
    run_id: str,
    thread_id: str,
    node_name: str | None = None,
    status: str = "running",
    message: str = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "id": f"workflow-event-{len(events) + 1:04d}",
        "protocol": WORKFLOW_EVENT_PROTOCOL,
        "sequence": len(events) + 1,
        "type": event_type,
        "runId": run_id,
        "threadId": thread_id,
        "status": status,
        "message": message,
        "nodeName": node_name,
        "node": (
            {"id": node_name, "label": _workflow_node_label(node_name)}
            if node_name
            else None
        ),
        "data": data or {},
    }
    events.append(event)
    return event


def _workflow_summary(
    result: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    status = str(result.get("status") or "completed")
    completed_nodes = [
        event for event in events if event.get("type") == "workflow.node.completed"
    ]
    failed_events = [event for event in events if str(event.get("status")) == "failed"]
    test_report = (
        result.get("test_report") if isinstance(result.get("test_report"), dict) else {}
    )
    test_summary = (
        test_report.get("summary")
        if isinstance(test_report.get("summary"), dict)
        else {}
    )
    build_summary = (
        result.get("build_summary")
        if isinstance(result.get("build_summary"), dict)
        else {}
    )
    clarification = (
        result.get("clarification")
        if isinstance(result.get("clarification"), dict)
        else {}
    )
    artifacts = _workflow_artifacts(result)
    code_changes = _workflow_code_changes(result)
    if status == "requires_user_input":
        question_count = len(clarification.get("questions", []))
        message = f"Workflow 等待用户确认/补充：完成 {len(completed_nodes)} 个节点，待确认问题 {question_count} 个。"
    else:
        message = (
            f"Workflow {status}：完成 {len(completed_nodes)} 个节点，"
            f"质量门禁={'通过' if result.get('quality_gate_passed') else '未通过'}。"
        )
    if result.get("preview_url"):
        message += f" 预览地址：{result.get('preview_url')}。"

    return {
        "status": status,
        "phase": result.get("phase"),
        "message": message,
        "completedNodeCount": len(completed_nodes),
        "failedEventCount": len(failed_events),
        "timeline": result.get("timeline", []),
        "qualityGatePassed": result.get("quality_gate_passed"),
        "needsRevision": result.get("needs_revision"),
        "previewUrl": result.get("preview_url"),
        "launchResult": result.get("launch_result"),
        "acceptanceRequest": result.get("acceptance_request"),
        "integrationNextAction": result.get("integration_next_action"),
        "repairIteration": result.get("repair_iteration"),
        "maxRepairIterations": result.get("max_repair_iterations"),
        "buildSummary": build_summary,
        "testSummary": test_summary,
        "codeChangesSummary": code_changes.get("summary") if code_changes else None,
        "artifacts": artifacts,
        "clarification": clarification,
        "observability": result.get("observability", {}),
    }


def _workflow_visual_payload(
    *,
    run_id: str,
    thread_id: str,
    summary: dict[str, Any],
    events: list[dict[str, Any]],
    result: dict[str, Any],
) -> dict[str, Any]:
    """构建 CustomEvent 与 StateSnapshot 共用的标准可视化数据。"""

    code_changes = _workflow_code_changes(result)
    confirmation_artifact = _workflow_confirmation_artifact(result)
    state_payload = {
        "status": summary.get("status"),
        "request": result.get("request"),
        "phase": summary.get("phase"),
        "timeline": summary.get("timeline", []),
        "artifacts": summary.get("artifacts", {}),
        "observability": summary.get("observability", {}),
        "qualityGatePassed": summary.get("qualityGatePassed"),
        "needsRevision": summary.get("needsRevision"),
        "previewUrl": summary.get("previewUrl"),
        "tasks": result.get("tasks", []),
        "buildSummary": result.get("build_summary", {}),
        "testReport": result.get("test_report", {}),
        "repairTaskPlan": result.get("repair_task_plan"),
        "clarification": result.get("clarification", {}),
        "project_plan": result.get("project_plan"),
        "pending_project_plan": result.get("pending_project_plan"),
        "project_plan_path": result.get("project_plan_path"),
        "detail_selection": result.get("detail_selection"),
        "selectedPageId": result.get("selectedPageId"),
        "selected_data_source_id": result.get("selected_data_source_id"),
        "selectedSkillNames": result.get("selected_skill_names", []),
    }
    payload = {
        "runId": run_id,
        "threadId": thread_id,
        "summary": summary,
        "events": events,
        "state": state_payload,
        "result": _public_workflow_state(result),
    }
    if code_changes:
        payload["codeChanges"] = code_changes
        state_payload["codeChanges"] = code_changes
    if confirmation_artifact:
        payload["confirmationArtifact"] = confirmation_artifact
    return payload


def _workflow_confirmation_artifact(
    result: dict[str, Any],
) -> dict[str, str] | None:
    """返回当前确认门禁对应的唯一 Markdown 文档。"""

    if result.get("status") != "requires_user_input":
        return None

    clarification = result.get("clarification")
    if not isinstance(clarification, dict):
        return None
    if clarification.get("status") != "requires_user_input":
        return None

    artifact_contracts = {
        "requirement_spec_confirmation": {
            "phase": "requirements",
            "id": "requirement_spec",
            "name": "requirement-spec.md",
            "path_field": "requirement_spec_path",
        },
        "project_plan_confirmation": {
            "phase": "project_planning",
            "id": "project_plan",
            "name": "project-plan.md",
            "path_field": "project_plan_path",
        },
    }
    contract = artifact_contracts.get(str(clarification.get("mode") or ""))
    if not contract or result.get("phase") != contract["phase"]:
        return None

    raw_path = result.get(contract["path_field"])
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path)
    if path.name != contract["name"] or not path.is_file():
        return None

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    return {
        "id": contract["id"],
        "name": contract["name"],
        "path": str(path),
        "format": "markdown",
        "content": content,
    }


def _workflow_code_changes(value: dict[str, Any]) -> dict[str, Any] | None:
    code_change_sets = value.get("code_change_sets")
    if isinstance(code_change_sets, list):
        merged = merge_code_change_sets(
            item for item in code_change_sets if isinstance(item, dict)
        )
        if merged:
            return merged

    code_changes = value.get("code_changes")
    if (
        isinstance(code_changes, dict)
        and isinstance(code_changes.get("files"), list)
        and code_changes.get("files")
    ):
        return code_changes

    return None
