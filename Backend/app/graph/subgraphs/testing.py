from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents.repair_planner import plan_repairs_with_repair_planner_agent
from app.agents.test.validator import summarize_tests_with_deep_agent
from app.graph.nodes.common import capture_agent_file_changes, workspace_from_state
from app.graph.state import ProjectState
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.integration_test_runner import run_integration_checks
from app.services.test_validation import evaluate_quality_gate
from app.workspace.code_changes import code_change_state_update
from app.workspace.test_documents import write_test_report_json
from app.workspace.task_documents import write_repair_task_plan_json


def _build_is_clean(state: ProjectState) -> bool:
    summary = state.get("build_summary", {})
    return int(summary.get("failed", 0)) == 0 and int(summary.get("pending", 0)) == 0


def _append_check(state: ProjectState, check: dict) -> list[dict]:
    return [*state.get("test_results", []), check]


def actual_project_checks(state: ProjectState) -> dict:
    result = run_integration_checks(state)
    return {
        "test_results": [
            *state.get("test_results", []),
            *result.get("test_results", []),
        ],
        "test_events": result.get("test_events", []),
    }


def api_contract_check(state: ProjectState) -> dict:
    errors = validate_api_contract_consistency(state.get("project_plan", {}))
    passed = _build_is_clean(state) and not errors
    return {
        "test_results": _append_check(
            state,
            {
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
                "failure_category": None if passed else "contract_mismatch",
                "execution": {
                    "tool": "deterministic_validator",
                    "argv": ["project-plan-contract-validation"],
                    "cwd": ".",
                    "returncode": 0 if passed else 1,
                    "timed_out": False,
                    "stdout_log": None,
                    "stderr_log": None,
                },
            },
        ),
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
    if state.get("quality_gate_passed"):
        return {
            "repair_task_plan": {},
            "repair_tasks": [],
            "integration_next_action": "launch_project",
            "test_events": ["repair_planning:skipped"],
        }

    repair_iteration = int(state.get("repair_iteration", 0) or 0)
    max_repair_iterations = int(state.get("max_repair_iterations", 3) or 3)
    if repair_iteration >= max_repair_iterations:
        return {
            "repair_task_plan": {
                "version": "0.1.0",
                "status": "terminal_failure",
                "decision": "terminal_failure",
                "reason": "Integration repair iteration budget exhausted.",
                "tasks": [],
            },
            "repair_tasks": [],
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
        "repair_iteration": repair_iteration + 1 if next_action == "repair_build" else repair_iteration,
        "max_repair_iterations": max_repair_iterations,
        "integration_next_action": next_action,
        "test_events": ["repair_planning"],
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
    result = _testing_subgraph.invoke(
        {
            **state,
            "test_results": [],
            "test_events": [],
            "code_changes": {},
            "code_change_sets": [],
            "timeline": [],
        }
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
