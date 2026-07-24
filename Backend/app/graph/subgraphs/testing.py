from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.agents.repair_planner import plan_repairs_with_repair_planner_agent
from app.agents.test.validator import summarize_tests_with_deep_agent
from app.graph.nodes.common import capture_agent_file_changes, workspace_from_state
from app.graph.nodes.confirmation import extract_confirmation_answer, user_confirmed_text
from app.graph.state import ProjectState
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.integration_test_runner import report_check_progress, run_integration_checks
from app.services.test_validation import evaluate_quality_gate
from app.workspace.code_changes import code_change_state_update
from app.workspace.test_documents import write_test_report_json
from app.workspace.task_documents import write_repair_task_plan_json


INTEGRATION_TEST_PROGRESS_REPORTER_KEY = "integration_test_progress_reporter"
IntegrationTestProgressReporter = Callable[[dict[str, Any]], None]


def _build_is_clean(state: ProjectState) -> bool:
    summary = state.get("build_summary", {})
    return int(summary.get("failed", 0)) == 0 and int(summary.get("pending", 0)) == 0


def _append_check(state: ProjectState, check: dict) -> list[dict]:
    return [*state.get("test_results", []), check]


def _progress_reporter(config: RunnableConfig | None) -> IntegrationTestProgressReporter | None:
    """从运行配置读取瞬态进度回调，避免将回调写入可持久化 Graph State。"""

    configurable = config.get("configurable", {}) if config else {}
    reporter = configurable.get(INTEGRATION_TEST_PROGRESS_REPORTER_KEY)
    return reporter if callable(reporter) else None


def _check_progress_snapshot_writer() -> IntegrationTestProgressReporter:
    """将检查增量合并为小型快照，并通过 LangGraph custom stream 发送。"""

    try:
        writer = get_stream_writer()
    except RuntimeError:
        writer = lambda _: None
    checks: dict[str, dict[str, Any]] = {}

    def report(event: dict[str, Any]) -> None:
        """按稳定检查标识更新快照，确保前端不会为同一检查新增重复条目。"""

        check = event.get("check")
        if not isinstance(check, dict):
            return
        check_id = str(check.get("id") or "").strip()
        status = str(event.get("status") or "").strip()
        if not check_id or status not in {"running", "passed", "skipped", "failed"}:
            return
        checks[check_id] = {
            "id": check_id,
            "name": str(check.get("name") or check_id),
            "status": status,
            "required": bool(check.get("required")),
            "evidence": str(check.get("evidence") or "")[:1_000],
        }
        writer(
            {
                "type": "integration_test.checks",
                "checks": list(checks.values()),
            }
        )

    return report


def actual_project_checks(
    state: ProjectState,
    config: RunnableConfig,
) -> dict:
    """执行真实项目检查，并把每项命令的进度交给外层工作流流式展示。"""

    result = run_integration_checks(state, on_progress=_progress_reporter(config))
    return {
        "test_results": [
            *state.get("test_results", []),
            *result.get("test_results", []),
        ],
        "test_events": result.get("test_events", []),
    }


def api_contract_check(
    state: ProjectState,
    config: RunnableConfig,
) -> dict:
    """校验正式 ProjectPlan 契约，并区分计划错误与未完成构建。"""

    reporter = _progress_reporter(config)
    report_check_progress(
        reporter,
        status="running",
        check={
            "id": "api_contract",
            "name": "API 契约有效",
            "required": True,
            "skipped": False,
            "evidence": "正在校验 API 契约。",
        },
    )
    errors = validate_api_contract_consistency(state.get("project_plan", {}))
    passed = _build_is_clean(state) and not errors
    check = {
        "id": "api_contract",
        "name": "API 契约有效",
        "layer": "contract",
        "language": None,
        "passed": passed,
        "skipped": False,
        "required": True,
        "command": "project-plan-contract-validation",
        "evidence": (
            "API contract schemas, data-source refs, endpoint dependencies, and page field bindings are consistent."
            if passed
            else "; ".join(errors)
            or f"Build summary is not clean: {state.get('build_summary', {})}"
        ),
        "failure_category": (
            None
            if passed
            else "contract_mismatch"
            if errors
            else "build_incomplete"
        ),
        "execution": {
            "tool": "deterministic_validator",
            "argv": ["project-plan-contract-validation"],
            "cwd": ".",
            "returncode": 0 if passed else 1,
            "timed_out": False,
            "stdout_log": None,
            "stderr_log": None,
        },
    }
    report_check_progress(reporter, status="passed" if passed else "failed", check=check)
    return {
        "test_results": _append_check(state, check),
        "test_events": ["api_contract"],
    }


def test_agent_review(state: ProjectState) -> dict:
    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="test.deep_agent",
        action=lambda: summarize_tests_with_deep_agent(
            test_results=state.get("test_results", []),
            build_results=state.get("build_results", []),
            workspace=workspace,
            selected_skill_names=state.get("selected_skill_names"),
        ),
    )
    return {
        **code_change_state_update(captured.code_change_set),
        "test_agent_review": captured.value,
        "test_events": ["test_agent_review"],
    }


def main_quality_gate(state: ProjectState) -> dict:
    review = state.get("test_agent_review", {})
    report = evaluate_quality_gate(
        test_results=state.get("test_results", []),
        agent_note=review.get("agent_note", "Test Agent completed without a note."),
    )
    report["reviewed_by"] = review.get("reviewed_by")
    report_path = write_test_report_json(state, report)
    return {
        "phase": "integration_test",
        "test_report": report,
        "test_report_path": report_path,
        "quality_gate_passed": report["passed"],
        "needs_revision": report["needs_revision"],
        "revision_requests": report["revision_requests"],
        "test_events": ["main_quality_gate"],
    }


def repair_planning(state: ProjectState) -> dict:
    """根据质量门禁结果选择 RepairPlanner 修复任务或终止路径。"""

    if state.get("quality_gate_passed"):
        return {
            "repair_task_plan": {},
            "repair_tasks": [],
            "integration_next_action": "launch_project",
            "test_events": ["repair_planning:skipped"],
        }

    existing_plan = state.get("repair_task_plan")
    request = str(state.get("request") or "")
    if (
        isinstance(existing_plan, dict)
        and existing_plan.get("decision") == "requires_user_confirmation"
    ):
        answer = extract_confirmation_answer(request).replace(" ", "")
        if any(signal in answer for signal in ("拒绝", "不同意", "不批准")):
            rejected_plan = {
                **existing_plan,
                "status": "terminal_failure",
                "decision": "terminal_failure",
                "tasks": [],
            }
            return {
                "repair_task_plan": rejected_plan,
                "repair_tasks": [],
                "integration_next_action": "handle_failure",
                "clarification": {},
                "test_events": ["repair_planning:scope_rejected"],
            }
        if user_confirmed_text(
            request,
            positive_signals=("批准", "同意", "确认"),
            negative_signals=("拒绝", "不同意", "不批准"),
        ):
            approved_tasks = [
                task
                for task in existing_plan.get("candidateTasks", [])
                if isinstance(task, dict)
            ]
            approved_plan = {
                **existing_plan,
                "status": "ready" if approved_tasks else "terminal_failure",
                "decision": "repair" if approved_tasks else "terminal_failure",
                "tasks": approved_tasks,
                "approvedPlanId": existing_plan.get("planId"),
            }
            return {
                "repair_task_plan": approved_plan,
                "repair_tasks": approved_tasks,
                "integration_next_action": "repair_build" if approved_tasks else "handle_failure",
                "clarification": {},
                "test_events": ["repair_planning:scope_approved"],
            }

    repair_iteration = int(state.get("repair_iteration", 0) or 0)
    max_repair_iterations = int(state.get("max_repair_iterations", 3) or 3)
    if repair_iteration >= max_repair_iterations:
        repair_task_plan = {
            "version": "0.1.0",
            "status": "terminal_failure",
            "decision": "terminal_failure",
            "reason": "Integration repair iteration budget exhausted.",
            "tasks": [],
        }
        repair_task_plan_path = write_repair_task_plan_json(state, repair_task_plan)
        return {
            "repair_task_plan": repair_task_plan,
            "repair_task_plan_path": repair_task_plan_path,
            "repair_tasks": [],
            "repair_iteration": repair_iteration,
            "max_repair_iterations": max_repair_iterations,
            "integration_next_action": "handle_failure",
            "test_events": ["repair_planning:budget_exhausted"],
        }

    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="repair_planner.deep_agent",
        action=lambda: plan_repairs_with_repair_planner_agent(
            test_report=state.get("test_report", {}),
            revision_requests=state.get("revision_requests", []),
            build_task_plan=state.get("build_task_plan"),
            build_execution_scope=state.get("build_execution_scope"),
            scoped_tasks=(state.get("build_execution_slice") or {}).get("tasks", []),
            repair_attempt=repair_iteration + 1,
            workspace=workspace,
            selected_skill_names=state.get("selected_skill_names"),
        ),
    )
    repair_task_plan = captured.value
    repair_task_plan_path = write_repair_task_plan_json(state, repair_task_plan)
    next_action = _next_action_for_repair_plan(repair_task_plan)
    return {
        **code_change_state_update(captured.code_change_set),
        "repair_task_plan": repair_task_plan,
        "repair_task_plan_path": repair_task_plan_path,
        "repair_tasks": repair_task_plan.get("tasks", []),
        "repair_iteration": repair_iteration,
        "max_repair_iterations": max_repair_iterations,
        "integration_next_action": next_action,
        "clarification": (
            _repair_scope_confirmation_payload(repair_task_plan)
            if next_action == "await_user_input"
            else {}
        ),
        "test_events": ["repair_planning"],
    }


def _repair_scope_confirmation_payload(repair_task_plan: dict[str, Any]) -> dict[str, Any]:
    """构造集成测试修复范围的 AG-UI 确认载荷。"""

    plan_id = str(repair_task_plan.get("planId") or "")
    requested_paths = [
        str(path) for path in repair_task_plan.get("requestedPaths", []) if str(path).strip()
    ]
    requested_resources = [
        dict(item)
        for item in repair_task_plan.get("requestedResources", [])
        if isinstance(item, dict)
    ]
    reason = str(repair_task_plan.get("reason") or "修复需要用户批准范围。")
    return {
        "mode": "repair_scope_confirmation",
        "status": "requires_user_input",
        "message": "测试修复计划请求确认代码修改范围。",
        "planId": plan_id,
        "requestedPaths": requested_paths,
        "requestedResources": requested_resources,
        "reason": reason,
        "questions": [
            {
                "id": "repair_scope_confirmation",
                "header": "修复范围",
                "question": (
                    f"计划 {plan_id} 请求修改：{'、'.join(requested_paths) or '未提供额外路径'}。"
                    f"原因：{reason}。是否批准？"
                ),
                "type": "text",
                "placeholder": "回复“批准修复范围”或“拒绝修复范围”。",
            }
        ],
    }


def _next_action_for_repair_plan(repair_task_plan: dict) -> str:
    decision = repair_task_plan.get("decision")
    status = repair_task_plan.get("status")
    if decision == "requires_user_confirmation" or status == "requires_user_confirmation":
        return "await_user_input"
    if decision == "terminal_failure" or status == "terminal_failure":
        return "handle_failure"
    if repair_task_plan.get("tasks"):
        return "repair_build"
    return "handle_failure"


def build_testing_subgraph():
    builder = StateGraph(ProjectState)

    builder.add_node("actual_project_checks", actual_project_checks)
    builder.add_node("api_contract_check", api_contract_check)
    builder.add_node("test_agent_review", test_agent_review)
    builder.add_node("main_quality_gate", main_quality_gate)
    builder.add_node("repair_planning", repair_planning)

    builder.add_edge(START, "actual_project_checks")
    builder.add_edge("actual_project_checks", "api_contract_check")
    builder.add_edge("api_contract_check", "test_agent_review")
    builder.add_edge("test_agent_review", "main_quality_gate")
    builder.add_edge("main_quality_gate", "repair_planning")
    builder.add_edge("repair_planning", END)

    return builder.compile()


_testing_subgraph = build_testing_subgraph()


def integration_test(state: ProjectState) -> dict:
    """运行测试子图，并把内部检查的增量状态转发到主 Graph 流。"""

    result = _testing_subgraph.invoke(
        {
            **state,
            "test_results": [],
            "test_events": [],
            "code_changes": {},
            "code_change_sets": [],
            "timeline": [],
        },
        config={
            "configurable": {
                INTEGRATION_TEST_PROGRESS_REPORTER_KEY: _check_progress_snapshot_writer(),
            }
        },
    )
    return {
        "phase": "integration_test",
        "test_results": result.get("test_results", []),
        "test_events": result.get("test_events", []),
        "test_agent_review": result.get("test_agent_review", {}),
        "test_report": result.get("test_report", {}),
        "test_report_path": result.get("test_report_path"),
        "quality_gate_passed": result.get("quality_gate_passed", False),
        "needs_revision": result.get("needs_revision", False),
        "revision_requests": result.get("revision_requests", []),
        "repair_task_plan": result.get("repair_task_plan", {}),
        "repair_task_plan_path": result.get("repair_task_plan_path"),
        "repair_tasks": result.get("repair_tasks", []),
        "repair_iteration": result.get("repair_iteration", state.get("repair_iteration", 0)),
        "max_repair_iterations": result.get(
            "max_repair_iterations", state.get("max_repair_iterations", 3)
        ),
        "integration_next_action": result.get(
            "integration_next_action",
            "launch_project" if result.get("quality_gate_passed", False) else "handle_failure",
        ),
        "code_changes": result.get("code_changes", {}),
        "code_change_sets": result.get("code_change_sets", []),
        "timeline": ["integration_test"],
    }
