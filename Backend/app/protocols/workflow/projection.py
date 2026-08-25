"""将主工作流内部状态映射为稳定且可安全发送给前端的数据。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.protocols.workflow.definition import (
    WORKFLOW_ARTIFACT_FIELDS,
    WORKFLOW_EVENT_PROTOCOL,
    WORKFLOW_NODE_LABELS,
    WORKFLOW_STATIC_NEXT_NODES,
)
from app.workspace.code_changes import merge_code_change_sets


_CODE_REVIEW_VISIBLE_PHASES = {
    "code_review",
    "launch_project",
    "acceptance",
    "finalize_project",
    "completed",
}


def _requirements_confirmation_projection(result: dict[str, Any]) -> dict[str, bool]:
    """仅在当前快照明确携带需求确认字段时公开布尔状态，避免增量帧把缺失误报为未确认。"""

    if "requirements_confirmed" not in result:
        return {}
    return {"requirementsConfirmed": result.get("requirements_confirmed") is True}


def _workflow_test_target(result: dict[str, Any]) -> dict[str, Any] | None:
    """从节点结果或 clarification 统一读取测试目标，保证增量帧完整投影。"""

    target = result.get("test_target")
    if isinstance(target, dict):
        return target
    clarification = result.get("clarification")
    if isinstance(clarification, dict) and isinstance(clarification.get("testTarget"), dict):
        return clarification.get("testTarget")
    return None


def _workflow_code_review_result(value: Any) -> dict[str, Any]:
    """将代码审查内部 snake_case 结果投影为有界的 AG-UI camelCase 结构。"""

    if not isinstance(value, dict):
        return {}
    raw_targets = value.get("targets")
    targets: list[dict[str, Any]] = []
    if isinstance(raw_targets, list):
        for raw in raw_targets[:2]:
            if not isinstance(raw, dict):
                continue
            target = {
                "side": raw.get("side"),
                "root": raw.get("root"),
                "status": raw.get("status"),
                "scannedFileCount": raw.get(
                    "scanned_file_count", raw.get("scannedFileCount", 0)
                ),
            }
            if raw.get("warning"):
                target["warning"] = raw.get("warning")
            targets.append(target)
    raw_issues = value.get("issues")
    issues: list[dict[str, Any]] = []
    if isinstance(raw_issues, list):
        for raw in raw_issues[:100]:
            if not isinstance(raw, dict):
                continue
            issue: dict[str, Any] = {
                "id": raw.get("id"),
                "side": raw.get("side"),
                "severity": raw.get("severity"),
                "title": raw.get("title"),
                "summary": raw.get("summary"),
                "file": raw.get("file"),
            }
            rule_id = raw.get("rule_id", raw.get("ruleId"))
            if rule_id:
                issue["ruleId"] = rule_id
            line = raw.get("line")
            if isinstance(line, int) and not isinstance(line, bool) and line > 0:
                issue["line"] = line
            issues.append(issue)
    return {
        "status": value.get("status", "completed"),
        "summary": value.get("summary", ""),
        "issueCount": value.get("issue_count", value.get("issueCount", len(issues))),
        "truncated": bool(value.get("truncated")),
        "loadedSkills": value.get("loaded_skills", value.get("loadedSkills", [])),
        "targets": targets,
        "issues": issues,
    }


def _workflow_code_review_result_for_phase(
    value: Any,
    phase: Any,
) -> dict[str, Any]:
    """仅在审查节点及其后续交付节点公开代码审查结果。"""

    if str(phase or "") not in _CODE_REVIEW_VISIBLE_PHASES:
        return {}
    return _workflow_code_review_result(value)


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
    started_node = (
        node.get("id")
        if last_event.get("type") == "workflow.node.started"
        else None
    )
    code_changes = _workflow_code_changes(result)

    phase = started_node or result.get("phase") or node.get("id")
    return {
        "status": last_event.get("status") or result.get("status") or "running",
        # 下一节点刚开始时，result 仍属于上一节点，必须优先展示正在执行的节点。
        "phase": phase,
        "message": last_event.get("message") or "Workflow is running.",
        "completedNodeCount": len(completed_nodes),
        "failedEventCount": len(failed_events),
        "timeline": result.get("timeline", []),
        "qualityGatePassed": result.get("quality_gate_passed"),
        "needsRevision": result.get("needs_revision"),
        "previewUrl": (
            result.get("preview_url") if _preview_visible_for_phase(phase) else None
        ),
        "buildSummary": result.get("build_summary", {}),
        "buildTaskPlan": result.get("build_task_plan", {}),
        "buildExecutionScope": result.get("build_execution_scope"),
        "buildTaskPlanConfirmation": (
            result.get("build_task_plan_confirmation")
            or (
                result.get("clarification")
                if isinstance(result.get("clarification"), dict)
                and result.get("clarification", {}).get("mode")
                == "build_task_plan_confirmation"
                else {}
            )
        ),
        "testTarget": _workflow_test_target(result),
        "reviewPhaseConfirmation": result.get("review_phase_confirmation", {}),
        "codeReviewResult": _workflow_code_review_result_for_phase(
            result.get("code_review_result"),
            phase,
        ),
        "testSummary": {},
        "unitTestSummary": result.get("unit_test_report", {}),
        "unitTestReport": result.get("unit_test_report", {}),
        "unitTestResults": result.get("unit_test_results", []),
        "unitTestQualityGatePassed": result.get("unit_test_quality_gate_passed"),
        "unitTestGatePassed": result.get("unit_test_gate_passed"),
        "unitTestNextAction": result.get("unit_test_next_action"),
        "smallTaskTasks": result.get("small_task_tasks", []),
        "smallTaskResults": result.get("small_task_results", []),
        "smallTaskHandoff": result.get("small_task_handoff", {}),
        "codeChangesSummary": code_changes.get("summary") if code_changes else None,
        "artifacts": _workflow_artifacts(result),
        "clarification": result.get("clarification", {}),
        "lifecycle": result.get("lifecycle"),
        **_requirements_confirmation_projection(result),
        "workspaceInspectionProgress": result.get("workspace_scan_progress"),
    }


def _workflow_node_label(node_name: str) -> str:
    return WORKFLOW_NODE_LABELS.get(node_name, node_name)


def _preview_visible_for_phase(phase: Any) -> bool:
    """仅允许启动预览及其后续阶段向前端公开预览相关状态。"""

    return str(phase or "") in {"launch_project", "acceptance", "completed"}


def _workflow_start_node(
    resume_from: str | None,
    workflow_scope: str | None = None,
) -> str:
    """返回开发就绪检查、实体绑定或其后的主 Workflow 展示入口。"""

    if workflow_scope == "application_planning":
        return (
            resume_from
            if resume_from
            in {
                "design_intent_analysis",
                "requirements",
                "product_planning",
                "ui_confirmation",
                "technical_planning",
                "project_planning",
            }
            else "requirements"
        )
    if resume_from == "inspect_database_context":
        return "prepare_build_tasks"
    supported = set(WORKFLOW_NODE_LABELS) - {"handle_failure"}
    return resume_from if resume_from in supported else "development_readiness_gate"


def _workflow_next_nodes(node_name: str, update: dict[str, Any]) -> list[str]:
    """仅预测下一个 UI 时间线节点，不参与 LangGraph 实际路由。"""

    if node_name == "design_intent_analysis":
        target = str(update.get("design_change_target") or "design_chat_response")
        return [target] if target in {
            "requirements",
            "product_planning",
            "ui_confirmation",
            "design_chat_response",
        } else ["design_chat_response"]
    if node_name == "ui_confirmation":
        # 仅 application_planning 使用：待确认时 Graph 走 END 等待用户，不发 started；
        # 用户确认全部设计稿后同 run 内流转到 project_planning，必须预测下一节点，
        # 否则 project_planning 执行期间前端 phase 仍停留在 ui_confirmation，
        # 误判为"设计稿生成中"继续渲染设计稿区域。
        if update.get("status") == "requires_user_input":
            return []
        return ["technical_planning"]
    if node_name == "product_planning":
        if update.get("status") == "requires_user_input":
            return []
        return ["ui_confirmation"]
    if node_name == "requirements":
        if update.get("status") == "requires_user_input":
            return []
        return ["product_planning"]
    if node_name == "technical_planning":
        return []
    if node_name == "integration_test":
        if update.get("quality_gate_passed"):
            return ["review_phase_confirmation"]
        next_action = update.get("integration_next_action")
        if next_action == "repair_build":
            return ["small_task_repair"]
        if next_action == "small_task_repair":
            return ["small_task_repair"]
        if next_action == "await_user_input":
            return []
        return ["handle_failure"]
    if node_name == "development_readiness_gate":
        if update.get("status") == "requires_user_input":
            return []
        return ["inspect_workspace"]
    if node_name == "unit_test":
        if update.get("status") == "requires_user_input":
            return []
        next_action = str(update.get("unit_test_next_action") or "")
        if next_action == "unit_test_repair":
            return ["unit_test_repair"]
        if next_action == "test_phase_confirmation" and update.get("unit_test_gate_passed") is True:
            return ["test_phase_confirmation"]
        return ["handle_failure"]
    if node_name == "unit_test_repair":
        if update.get("status") == "requires_user_input":
            return []
        return ["unit_test"] if update.get("small_task_route") == "unit_test" else []
    if node_name == "detail_confirmation":
        if update.get("status") == "requires_user_input":
            return []
        return ["inspect_workspace"]
    if node_name == "entity_source_binding":
        return []
    if node_name == "project_planning":
        if update.get("status") == "requires_user_input":
            return []
        return ["development_readiness_gate"]
    if node_name == "inspect_workspace":
        return ["prepare_build_tasks"]
    if node_name == "prepare_build_tasks":
        if update.get("status") == "requires_user_input":
            return []
        if update.get("status") == "failed":
            return ["handle_failure"]
        return ["build"]
    if node_name == "build":
        build_summary = update.get("build_summary")
        summary_status = (
            str(build_summary.get("status") or "")
            if isinstance(build_summary, dict)
            else ""
        )
        if update.get("status") == "failed":
            return ["handle_failure"]
        if summary_status == "completed":
            return ["unit_test"]
        if summary_status == "requires_confirmation" or update.get("status") == "requires_user_input":
            return []
        return ["handle_failure"]
    if node_name == "test_phase_confirmation":
        build_summary = update.get("build_summary")
        build_completed = (
            isinstance(build_summary, dict)
            and build_summary.get("status") == "completed"
        )
        return ["integration_test"] if (
            update.get("status") == "completed"
            and build_completed
            and update.get("unit_test_gate_passed") is True
        ) else []
    if node_name == "review_phase_confirmation":
        return ["code_review"] if update.get("status") == "completed" else []
    if node_name == "code_review":
        return ["launch_project"] if update.get("status") == "completed" else ["handle_failure"]
    if node_name == "launch_project":
        return []
    return WORKFLOW_STATIC_NEXT_NODES.get(node_name, [])


def _workflow_artifacts(value: dict[str, Any]) -> dict[str, Any]:
    return {
        field: value.get(field)
        for field in WORKFLOW_ARTIFACT_FIELDS
        if value.get(field) and not str(value.get(field)).lower().endswith(".json")
    }


def _public_workflow_state(
    value: dict[str, Any],
    *,
    phase: Any | None = None,
) -> dict[str, Any]:
    """在状态发送到前端前移除内部 JSON 工件路径。"""

    public_state = {
        key: item
        for key, item in value.items()
        if key
        not in {
            "requirement_spec_json_path",
            "project_plan_json_path",
        }
        and not (key.endswith("_path") and str(item).lower().endswith(".json"))
    }
    inspection = _workspace_inspection_snapshot(value)
    if inspection is not None:
        public_state["workspaceInspection"] = inspection
    if "requirements_confirmed" in value:
        public_state["requirementsConfirmed"] = value.get("requirements_confirmed") is True
    if "code_review_result" in value:
        public_state.pop("code_review_result", None)
        public_state["codeReviewResult"] = _workflow_code_review_result_for_phase(
            value.get("code_review_result"),
            phase if phase is not None else value.get("phase"),
        )
    return public_state


def _workflow_node_detail(node_name: str, update: dict[str, Any]) -> dict[str, Any]:
    """把节点内部更新投射为前端可展示的摘要和结构化数据。"""

    if node_name == "review_phase_confirmation":
        clarification = update.get("clarification")
        clarification = clarification if isinstance(clarification, dict) else {}
        return {
            "message": clarification.get("message") or "测试已通过，等待确认进入审查阶段。",
            "data": {
                "clarification": clarification,
                "requiresUserInput": update.get("status") == "requires_user_input",
            },
        }
    if node_name == "code_review":
        result = _workflow_code_review_result(update.get("code_review_result"))
        return {
            "message": str(
                update.get("message") or result.get("summary") or "前后端代码审查完成。"
            ),
            "data": {
                "codeReviewResult": result,
                "requiresUserInput": False,
            },
        }
    if node_name == "classify_request_complexity":
        return {
            "message": f"复杂度={update.get('request_complexity')}，原因={update.get('complexity_reason')}",
            "data": {
                "requestComplexity": update.get("request_complexity"),
                "complexityDecision": update.get("complexity_decision"),
            },
        }
    if node_name == "design_intent_analysis":
        return {
            "message": (
                f"设计变更目标={update.get('design_change_target')}，"
                f"原因={update.get('design_change_reason')}"
            ),
            "data": {
                "target": update.get("design_change_target"),
                "reason": update.get("design_change_reason"),
                "affectedPageIds": update.get("design_change_affected_page_ids", []),
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
        if update.get("requirements_confirmed") is True:
            message = f"需求文档={update.get('requirement_spec_path')}"
        elif questions:
            message = "需求信息尚不完整，等待用户补充；补充完成前不生成需求文档草稿"
        else:
            message = "需求草稿已写入右侧，等待确认后转为正式需求文档"
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
                    "dagGeneration": update.get("dag_generation_progress"),
                },
            }
        return {
            "message": (
                f"计划文档={update.get('project_plan_path')}"
            ),
            "data": {"projectPlan": update.get("project_plan")},
        }
    if node_name == "product_planning":
        clarification = update.get("clarification")
        return {
            "message": f"产品规划={update.get('product_plan_path')}",
            "data": {
                "productPlan": update.get("product_plan"),
                "clarification": clarification,
                "requiresUserInput": update.get("status") == "requires_user_input",
            },
        }
    if node_name == "technical_planning":
        clarification = update.get("clarification")
        return {
            "message": f"技术规划={update.get('technical_plan_path') or update.get('project_plan_path')}",
            "data": {
                "technicalPlan": update.get("technical_plan") or update.get("project_plan"),
                "clarification": clarification,
                "requiresUserInput": update.get("status") == "requires_user_input",
            },
        }
    if node_name == "development_readiness_gate":
        clarification = update.get("clarification")
        return {
            "message": (
                "开发前置检查未通过，请先完成实体数据源绑定。"
                if update.get("status") == "requires_user_input"
                else "开发前置检查已通过。"
            ),
            "data": {
                "clarification": clarification,
                "requiresUserInput": update.get("status") == "requires_user_input",
                "developmentReadiness": update.get("development_readiness"),
            },
        }
    if node_name == "entity_source_binding":
        clarification = update.get("clarification")
        status = update.get("status")
        if status == "requires_user_input":
            return {
                "message": "实体数据源绑定待确认。",
                "data": {
                    "clarification": clarification,
                    "requiresUserInput": True,
                    "detailSelection": update.get("detail_selection"),
                },
            }
        return {
            "message": "实体数据源绑定已确认；请重新选择页面或 API 开始开发。",
            "data": {
                "detailSelection": update.get("detail_selection"),
                "detailPlans": update.get("detail_plans", []),
            },
        }
    if node_name == "inspect_workspace":
        workspace_inspection = _workspace_inspection_snapshot(update)
        if workspace_inspection is None:
            return {"message": "工作区代码扫描已完成", "data": {}}
        manifest = workspace_inspection["fileManifest"]
        graph = workspace_inspection["codeGraph"]
        message = (
            f"已索引 {manifest['totalFiles']} 个文件，"
            f"其中源文件 {manifest['sourceFiles']} 个，"
            f"识别 {len(workspace_inspection['techStack'])} 项技术栈和 "
            f"{len(workspace_inspection['entrypoints'])} 个入口"
        )
        if graph.get("available"):
            message += (
                f"，代码图解析 {graph.get('filesIndexed', 0)} 个文件，"
                f"建立 {graph.get('symbolsIndexed', 0)} 个节点和 "
                f"{graph.get('relationsIndexed', 0)} 条关系"
            )
        return {
            "message": message,
            "data": {"workspaceInspection": workspace_inspection},
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
                "message": _prepare_build_tasks_input_message(clarification, len(questions)),
                "data": {
                    "projectPlan": update.get("project_plan"),
                    "buildTaskPlan": update.get("build_task_plan"),
                    "buildExecutionScope": update.get("build_execution_scope"),
                    "buildTaskPlanConfirmation": update.get(
                        "build_task_plan_confirmation"
                    ) or clarification,
                    "clarification": clarification,
                    "requiresUserInput": True,
                    "dagGeneration": update.get("dag_generation_progress"),
                },
            }
        tasks = update.get("tasks") if isinstance(update.get("tasks"), list) else []
        if update.get("status") == "failed":
            return {
                "message": str(
                    update.get("message")
                    or "Build DAG 自动重生成失败，已停止代码生成。"
                ),
                "data": {
                    "buildTaskPlan": update.get("build_task_plan"),
                    "buildExecutionScope": update.get("build_execution_scope"),
                    "dagGeneration": update.get("dag_generation_progress"),
                    "error": update.get("error"),
                },
            }
        return {
            "message": f"任务数={len(tasks)}，任务 DAG 已按范围生成",
            "data": {
                "buildTaskPlan": update.get("build_task_plan"),
                "taskCount": len(tasks),
                "buildExecutionScope": update.get("build_execution_scope"),
                "buildTaskPlanConfirmation": update.get(
                    "build_task_plan_confirmation"
                ),
                "dagGeneration": update.get("dag_generation_progress"),
            },
        }
    if node_name == "build":
        summary = update.get("build_summary", {})
        return {
            "message": f"完成={summary.get('completed', 0)}，失败={summary.get('failed', 0)}",
            "data": {
                "buildSummary": summary,
                "buildExecutionSlice": update.get("build_execution_slice"),
                "buildEvents": update.get("build_events", []),
                "buildResults": update.get("build_results", []),
            },
        }
    if node_name == "unit_test":
        report = update.get("unit_test_report")
        report = report if isinstance(report, dict) else update.get("test_report", {})
        summary = report.get("summary", {}) if isinstance(report, dict) else {}
        clarification = update.get("clarification")
        clarification = clarification if isinstance(clarification, dict) else {}
        return {
            "message": clarification.get("message") or (
                f"通过={report.get('passed') if isinstance(report, dict) else None}，"
                f"检查={summary.get('passed', 0)}/{summary.get('total', 0)}"
            ),
            "data": {
                "unitTestReport": report,
                "unitTestResults": update.get("unit_test_results", []),
                "unitTestGeneration": update.get("unit_test_generation", {}),
                "unitTestGenerationContext": update.get(
                    "unit_test_generation_context", {}
                ),
                "unitTestNextAction": update.get("unit_test_next_action"),
                "unitTestRepairTaskPlan": update.get("unit_test_repair_task_plan"),
                "unitTestRepairIteration": update.get("unit_test_repair_iteration"),
                "unitTestMaxRepairIterations": update.get(
                    "unit_test_max_repair_iterations"
                ),
                "clarification": clarification,
                "requiresUserInput": update.get("status") == "requires_user_input",
            },
        }
    if node_name == "unit_test_repair":
        results = update.get("small_task_results")
        results = results if isinstance(results, list) else []
        tasks = update.get("small_task_tasks")
        tasks = tasks if isinstance(tasks, list) else update.get("repair_tasks", [])
        handoff = update.get("small_task_handoff")
        handoff = handoff if isinstance(handoff, dict) else {}
        return {
            "message": (
                str(update.get("message") or "")
                or f"SmallTask Agent 已处理 {len(results)} 个结果，准备重新执行单元测试。"
            ),
            "data": {
                "smallTaskTasks": tasks,
                "smallTaskResults": results,
                "smallTaskHandoff": handoff,
                "requiresUserInput": update.get("status") == "requires_user_input",
            },
        }
    if node_name == "test_phase_confirmation":
        clarification = update.get("clarification")
        clarification = clarification if isinstance(clarification, dict) else {}
        return {
            "message": clarification.get("message") or "开发已完成，等待确认进入测试阶段。",
            "data": {
                "clarification": clarification,
                "testTarget": update.get("test_target") or clarification.get("testTarget"),
                "requiresUserInput": update.get("status") == "requires_user_input",
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
        clarification = update.get("clarification")
        clarification_message = (
            str(clarification.get("message") or "")
            if isinstance(clarification, dict)
            else ""
        )
        return {
            "message": clarification_message or (
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
    if node_name == "small_task_repair":
        results = update.get("small_task_results")
        results = results if isinstance(results, list) else []
        tasks = update.get("small_task_tasks")
        tasks = tasks if isinstance(tasks, list) else update.get("repair_tasks", [])
        handoff = update.get("small_task_handoff")
        handoff = handoff if isinstance(handoff, dict) else {}
        return {
            "message": (
                str(update.get("message") or "")
                or f"SmallTask Agent 已处理 {len(results)} 个结果，剩余任务={len(tasks)}"
            ),
            "data": {
                "smallTaskTasks": tasks,
                "smallTaskResults": results,
                "smallTaskHandoff": handoff,
                "smallTaskBatch": update.get("small_task_batch", {}),
                "requiresUserInput": update.get("status") == "requires_user_input",
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


def _workspace_inspection_snapshot(update: dict[str, Any]) -> dict[str, Any] | None:
    """把工作区摘要裁剪为不含宿主机绝对路径的稳定展示结构。"""

    summary = update.get("workspace_snapshot_summary")
    if not isinstance(summary, dict):
        return None
    manifest = summary.get("file_manifest")
    manifest = manifest if isinstance(manifest, dict) else {}
    code_graph = summary.get("code_graph")
    code_graph = code_graph if isinstance(code_graph, dict) else {}
    code_graph_available = bool(code_graph.get("available"))
    timeline = update.get("timeline")
    timeline = timeline if isinstance(timeline, list) else []
    graph_payload: dict[str, Any] = {
        "provider": str(code_graph.get("provider") or "none")[:80],
        "providerVersion": str(code_graph.get("providerVersion") or "")[:40],
        "status": str(code_graph.get("status") or "unavailable")[:40],
        "available": code_graph_available,
        "buildType": str(code_graph.get("buildType") or "")[:40],
        "languages": _bounded_string_list(code_graph.get("languages"), limit=20),
        "message": str(code_graph.get("message") or "")[:300],
        "cacheHit": bool(code_graph.get("cacheHit")),
    }
    # 索引未完成或失败时不投影零值统计，避免 UI 把降级状态误读成空图。
    if code_graph_available:
        graph_payload.update(
            {
                "filesIndexed": _bounded_non_negative_int(code_graph.get("filesIndexed")),
                "symbolsIndexed": _bounded_non_negative_int(code_graph.get("symbolsIndexed")),
                "relationsIndexed": _bounded_non_negative_int(
                    code_graph.get("relationsIndexed")
                ),
                "nodesByKind": _safe_graph_distributions(code_graph.get("nodesByKind")),
                "relationsByKind": _safe_graph_distributions(
                    code_graph.get("relationsByKind")
                ),
                "sampleSymbols": _safe_graph_symbols(code_graph.get("sampleSymbols")),
                "warningCount": _bounded_non_negative_int(code_graph.get("warningCount")),
                "warnings": _bounded_string_list(code_graph.get("warnings"), limit=5),
                "durationMs": _bounded_non_negative_int(code_graph.get("durationMs")),
            }
        )
    snapshot = {
        "schemaVersion": str(summary.get("schema_version") or "")[:80],
        "revision": str(
            summary.get("workspace_revision") or update.get("workspace_revision") or ""
        )[:80],
        "cacheHit": "inspect_workspace:cache_hit" in timeline,
        "fileManifest": {
            "totalFiles": _bounded_non_negative_int(manifest.get("total_files_indexed")),
            "sourceFiles": _bounded_non_negative_int(manifest.get("source_files_indexed")),
            "truncated": bool(manifest.get("truncated")),
        },
        "techStack": _bounded_string_list(summary.get("tech_stack"), limit=40),
        "projectRoots": _safe_path_items(summary.get("project_roots"), limit=40),
        "entrypoints": _safe_path_items(summary.get("entrypoints"), limit=80),
        "codeGraph": graph_payload,
    }
    return _bound_workspace_inspection_extension(snapshot)


def _bound_workspace_inspection_extension(snapshot: dict[str, Any]) -> dict[str, Any]:
    """把代码图展示扩展控制在约十二 KB 内。"""

    def extension_size() -> int:
        """计算当前代码图扩展的 UTF-8 字符近似长度。"""

        return len(
            json.dumps(
                {"codeGraph": snapshot.get("codeGraph")},
                ensure_ascii=False,
            )
        )

    graph = snapshot.get("codeGraph")
    while extension_size() > 12_000:
        if isinstance(graph, dict) and graph.get("sampleSymbols"):
            graph["sampleSymbols"] = graph["sampleSymbols"][:-1]
        elif isinstance(graph, dict) and graph.get("relationsByKind"):
            graph["relationsByKind"] = graph["relationsByKind"][:-2]
        elif isinstance(graph, dict) and graph.get("nodesByKind"):
            graph["nodesByKind"] = graph["nodesByKind"][:-2]
        else:
            break
    return snapshot


def _safe_graph_distributions(value: Any) -> list[dict[str, Any]]:
    """裁剪节点或关系分类，防止第三方统计无界进入 AG-UI。"""

    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for raw_item in value[:12]:
        if not isinstance(raw_item, dict):
            continue
        kind = str(raw_item.get("kind") or "").strip()[:80]
        if not kind:
            continue
        items.append(
            {
                "kind": kind,
                "count": _bounded_non_negative_int(raw_item.get("count")),
            }
        )
    return items


def _safe_graph_symbols(value: Any) -> list[dict[str, Any]]:
    """裁剪代表性符号并只保留工作区相对路径和行号。"""

    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for raw_item in value[:8]:
        if not isinstance(raw_item, dict):
            continue
        path = _safe_relative_path(raw_item.get("path"))
        if not path:
            continue
        items.append(
            {
                "name": str(raw_item.get("name") or "")[:200],
                "kind": str(raw_item.get("kind") or "")[:80],
                "language": str(raw_item.get("language") or "")[:40],
                "path": path,
                "lineStart": _bounded_non_negative_int(raw_item.get("lineStart")),
                "lineEnd": _bounded_non_negative_int(raw_item.get("lineEnd")),
            }
        )
    return items


def _safe_relative_path(value: Any) -> str:
    """只接受工作区相对路径，拒绝绝对路径和路径穿越。"""

    path = str(value or "").strip().replace("\\", "/")
    if not path or path.startswith("/") or ":/" in path or ".." in Path(path).parts:
        return ""
    return path[:1_000]


def _bounded_non_negative_int(value: Any) -> int:
    """把外部计数规范为适合 UI 展示的非负整数。"""

    if not isinstance(value, (int, float)):
        return 0
    try:
        return max(0, min(int(value), 1_000_000))
    except (OverflowError, ValueError):
        return 0


def _bounded_string_list(value: Any, *, limit: int) -> list[str]:
    """裁剪字符串列表，避免工作流事件携带无界内容。"""

    if not isinstance(value, list):
        return []
    return [text for item in value if (text := str(item).strip()[:160])][:limit]


def _safe_path_items(value: Any, *, limit: int) -> list[dict[str, str]]:
    """仅保留安全的工作区相对路径及其类型标签。"""

    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for raw_item in value:
        if not isinstance(raw_item, dict):
            continue
        path = str(raw_item.get("path") or "").strip().replace("\\", "/")
        if not path or path.startswith("/") or ":/" in path or ".." in Path(path).parts:
            continue
        items.append(
            {
                "path": path[:1_000],
                "kind": str(raw_item.get("kind") or "unknown").strip()[:80],
            }
        )
        if len(items) >= limit:
            break
    return items


def _prepare_build_tasks_input_message(
    clarification: Any,
    question_count: int,
) -> str:
    """按 DAG 前置检查、校验失败和确认等待区分工作台提示。"""

    mode = clarification.get("mode") if isinstance(clarification, dict) else ""
    messages = {
        "build_task_plan_confirmation": "Build DAG 已生成，请确认任务规划后再进入 Build。",
        "build_prerequisite_error": "Build DAG 的正式产物或模板前置条件未满足，已返回上游流程。",
        "build_context_error": "当前构建范围缺少已确认的实体数据源绑定或技术契约。",
        "api_contract_consistency_error": "当前构建范围的 API 契约校验未通过，已阻止代码生成。",
        "build_task_plan_validation_error": "Build DAG 校验未通过，平台已停止代码生成。",
        "build_task_plan_generation_error": "Build DAG 自动重生成未得到有效任务计划，平台已停止代码生成。",
    }
    if mode in messages:
        return messages[mode]
    return f"当前构建准备需要输入，待确认问题={question_count}"


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
    attempt: int | None = None,
    iteration_kind: str | None = None,
    node_label: str | None = None,
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
            {"id": node_name, "label": node_label or _workflow_node_label(node_name)}
            if node_name
            else None
        ),
        "data": data or {},
    }
    if attempt is not None:
        event["attempt"] = attempt
    if iteration_kind:
        event["iterationKind"] = iteration_kind
    events.append(event)
    return event


def _workflow_summary(
    result: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    """生成最终 Workflow 摘要，并解释修复终止原因。"""

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
    conversation_response = str(result.get("conversation_response") or "").strip()
    if result.get("phase") == "design_chat_response" and conversation_response:
        message = conversation_response
    elif status == "requires_user_input":
        message = _workflow_user_input_message(result, clarification)
    else:
        message = f"Workflow {status}：完成 {len(completed_nodes)} 个节点。"
        quality_gate_passed = result.get("quality_gate_passed")
        # 创建规划不执行集成测试质量门；只有明确布尔结果才展示通过或未通过。
        if isinstance(quality_gate_passed, bool):
            message += f" 质量门禁={'通过' if quality_gate_passed else '未通过'}。"
        terminal_reason = _repair_terminal_reason(result)
        if terminal_reason:
            repair_iteration = result.get("repair_iteration")
            max_repair_iterations = result.get("max_repair_iterations")
            iteration_text = (
                f" 修复次数={repair_iteration}/{max_repair_iterations}。"
                if repair_iteration is not None and max_repair_iterations is not None
                else ""
            )
            message += f"{iteration_text} 终止原因：{terminal_reason}"
        retry_message = build_summary.get("retry_message")
        if retry_message:
            message = str(retry_message)
    # 只有启动预览及其后续阶段可以公开预览地址，避免重试集成测试时泄漏旧值。
    preview_visible = _preview_visible_for_phase(result.get("phase"))
    if preview_visible and result.get("preview_url") and status != "failed":
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
        "previewUrl": result.get("preview_url") if preview_visible else None,
        "launchResult": result.get("launch_result") if preview_visible else None,
        "acceptanceRequest": (
            result.get("acceptance_request") if preview_visible else None
        ),
        "integrationNextAction": result.get("integration_next_action"),
        "repairReturnNode": result.get("repair_return_node"),
        "repairIteration": result.get("repair_iteration"),
        "maxRepairIterations": result.get("max_repair_iterations"),
        "unitTestGatePassed": result.get("unit_test_gate_passed"),
        "unitTestNextAction": result.get("unit_test_next_action"),
        "unitTestRepairIteration": result.get("unit_test_repair_iteration"),
        "unitTestMaxRepairIterations": result.get("unit_test_max_repair_iterations"),
        "smallTaskTasks": result.get("small_task_tasks", []),
        "smallTaskResults": result.get("small_task_results", []),
        "smallTaskHandoff": result.get("small_task_handoff", {}),
        "buildSummary": build_summary,
        "buildTaskPlan": result.get("build_task_plan", {}),
        "buildExecutionScope": result.get("build_execution_scope"),
        "buildTaskPlanConfirmation": (
            result.get("build_task_plan_confirmation")
            or (
                clarification
                if clarification.get("mode") == "build_task_plan_confirmation"
                else {}
            )
        ),
        "testTarget": _workflow_test_target(result),
        "reviewPhaseConfirmation": result.get("review_phase_confirmation", {}),
        "codeReviewResult": _workflow_code_review_result_for_phase(
            result.get("code_review_result"),
            result.get("phase"),
        ),
        "testSummary": test_summary,
        "codeChangesSummary": code_changes.get("summary") if code_changes else None,
        "artifacts": artifacts,
        "clarification": clarification,
        "observability": result.get("observability", {}),
        "lifecycle": result.get("lifecycle"),
        **_requirements_confirmation_projection(result),
    }


def _workflow_user_input_message(
    result: dict[str, Any], clarification: dict[str, Any]
) -> str:
    """按实际门禁类型生成面向用户的等待提示，避免把验收误写成待补充问题。"""

    acceptance_request = result.get("acceptance_request")
    clarification_mode = str(clarification.get("mode") or "")
    if (
        clarification_mode == "page_acceptance"
        and isinstance(acceptance_request, dict)
        and acceptance_request
    ):
        return "项目预览已就绪，请确认是否符合预期。"

    confirmation_labels = {
        "requirement_document_confirmation": "需求文档草稿已生成，请确认后同时固化需求与页面操作规划。",
        "project_plan_confirmation": "项目计划已生成，请确认后继续。",
        "technical_plan_confirmation": "技术规划已生成，请确认后继续。",
        "technical_plan_generation_error": "技术规划未通过校验，请重新生成。",
        "batch_review": "页面与数据源设计已生成，请确认后继续。",
        "entity_source_binding": "实体数据源绑定已生成，请确认后继续。",
        "entity_source_binding_required": "请先完成当前目标依赖实体的数据源绑定。",
        "small_task_scope_confirmation": "小任务需要确认新增代码范围后继续。",
        "small_task_workflow_handoff": "小任务需要确认后转入正式工作流。",
        "unit_test_confirmation": "构建检查已完成。单元测试不是必需步骤，可能耗时较长，是否跳过单元测试？",
        "build_task_plan_confirmation": "Build DAG 已生成，请确认任务规划后再进入 Build。",
        "test_phase_confirmation": "开发已完成，请确认进入测试阶段。",
        "review_phase_confirmation": "测试已通过，请确认进入审查阶段。",
    }
    if clarification_mode in confirmation_labels:
        return confirmation_labels[clarification_mode]

    questions = clarification.get("questions")
    question_count = len(questions) if isinstance(questions, list) else 0
    if question_count > 0:
        return f"还有 {question_count} 个问题需要补充，完成后将继续执行。"

    return "当前阶段需要你的确认后继续。"


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
        "dagGeneration": result.get("dag_generation_progress"),
        "buildSummary": result.get("build_summary", {}),
        "buildTaskPlan": result.get("build_task_plan", {}),
        "buildExecutionScope": result.get("build_execution_scope"),
        "buildTaskPlanConfirmation": summary.get("buildTaskPlanConfirmation"),
        "testTarget": _workflow_test_target(result),
        "reviewPhaseConfirmation": result.get("review_phase_confirmation", {}),
        "codeReviewResult": _workflow_code_review_result_for_phase(
            result.get("code_review_result"),
            summary.get("phase"),
        ),
        "buildExecutionSlice": result.get("build_execution_slice"),
        "testReport": result.get("test_report", {}),
        "unitTestReport": result.get("unit_test_report", {}),
        "unitTestResults": result.get("unit_test_results", []),
        "unitTestQualityGatePassed": result.get("unit_test_quality_gate_passed"),
        "unitTestGeneration": result.get("unit_test_generation", {}),
        "unitTestGenerationContext": result.get("unit_test_generation_context", {}),
        "unitTestNextAction": result.get("unit_test_next_action"),
        "repairTaskPlan": result.get("repair_task_plan"),
        "smallTaskTasks": result.get("small_task_tasks", []),
        "smallTaskResults": result.get("small_task_results", []),
        "smallTaskHandoff": result.get("small_task_handoff", {}),
        "clarification": result.get("clarification", {}),
        **_requirements_confirmation_projection(result),
        "design_change_submission": result.get("design_change_submission", False),
        "design_change_request": result.get("design_change_request"),
        "design_change_target": result.get("design_change_target"),
        "design_change_reason": result.get("design_change_reason"),
        "design_change_existing_artifacts": result.get(
            "design_change_existing_artifacts", {}
        ),
        "product_plan": result.get("product_plan"),
        "product_plan_path": result.get("product_plan_path"),
        "technical_plan": result.get("technical_plan"),
        "technical_plan_path": result.get("technical_plan_path"),
        "project_plan": result.get("project_plan"),
        "pending_project_plan": result.get("pending_project_plan"),
        "project_plan_path": result.get("project_plan_path"),
        "detail_selection": result.get("detail_selection"),
        "selectedPageId": result.get("selectedPageId"),
        "selectedApiContractId": result.get("selected_api_contract_id"),
        "selectedEndpointId": result.get("selected_endpoint_id"),
        "detailTargetType": result.get("detail_target_type"),
        "buildExecutionScope": result.get("build_execution_scope"),
        "selectedSkillNames": result.get("selected_skill_names", []),
        "acceptanceAdjustment": result.get("acceptance_adjustment"),
        "lifecycle": result.get("lifecycle"),
        "ui_designs": result.get("ui_designs"),
        "workspaceInspectionProgress": result.get("workspace_scan_progress"),
    }
    payload = {
        "runId": run_id,
        "threadId": thread_id,
        "summary": summary,
        "events": events,
        "state": state_payload,
        "result": _public_workflow_state(result, phase=summary.get("phase")),
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
        "requirement_document_confirmation": {
            "phase": "product_planning",
            "id": "requirement_document",
            "name": "requirement-document",
            "path_field": "requirement_spec_path",
        },
        "project_plan_confirmation": {
            "phase": "project_planning",
            "id": "project_plan",
            "name": "project-plan.md",
            "path_field": "project_plan_path",
        },
        "technical_plan_confirmation": {
            "phase": "technical_planning",
            "id": "technical_plan",
            "name": "technical-plan.md",
            "path_field": "technical_plan_path",
        },
    }
    contract = artifact_contracts.get(str(clarification.get("mode") or ""))
    if not contract or result.get("phase") != contract["phase"]:
        return None
    raw_path = result.get(contract["path_field"])
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path)
    if (
        contract["id"] == "requirement_spec"
        and result.get("requirements_confirmed") is not True
        and "drafts" not in path.parts
    ):
        return None
    if contract["id"] == "product_plan" and "drafts" not in path.parts:
        return None
    if path.name != contract["name"] or not path.is_file():
        return None

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None

    if contract["id"] == "product_plan":
        # 需求与产品规划合并确认：确认卡与右侧文档展示同一份连贯的“需求文档”，
        # 需求草稿在前、产品规划内容作为后续章节接入（其 H1 降级为章节标题）。
        requirement_path_value = result.get("requirement_spec_path")
        requirement_content = ""
        if isinstance(requirement_path_value, str) and requirement_path_value.strip():
            requirement_path = Path(requirement_path_value)
            if (
                "drafts" in requirement_path.parts
                and requirement_path.name == "requirement-spec.md"
                and requirement_path.is_file()
            ):
                try:
                    requirement_content = requirement_path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    requirement_content = ""
        if requirement_content.strip():
            content = _merge_requirement_and_product_plan(requirement_content, content)

    return {
        "id": contract["id"],
        "name": contract["name"],
        "path": str(path),
        "format": "markdown",
        "content": content,
    }


def _merge_requirement_and_product_plan(requirement_content: str, product_plan_content: str) -> str:
    """把产品规划草稿接在需求草稿之后，呈现为一份连贯的需求文档。"""

    merged_requirement = requirement_content.rstrip()
    plan = product_plan_content.strip()
    lines = plan.splitlines()
    if lines and lines[0].lstrip().startswith("# ") and not lines[0].lstrip().startswith("## "):
        lines[0] = f"## {lines[0].lstrip()[2:].strip()}"
    return f"{merged_requirement}\n\n{'\n'.join(lines).strip()}\n"


def _repair_terminal_reason(result: dict[str, Any]) -> str:
    """提取修复计划中的终止原因，供最终摘要直接解释失败。"""

    repair_task_plan = result.get("repair_task_plan")
    if not isinstance(repair_task_plan, dict):
        return ""
    if repair_task_plan.get("status") != "terminal_failure":
        return ""
    return str(repair_task_plan.get("reason") or "").strip()


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
