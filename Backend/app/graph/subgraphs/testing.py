from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents.main.repair_planner import plan_repairs_with_main_agent
from app.agents.test.validator import summarize_tests_with_deep_agent
from app.graph.nodes.common import workspace_from_state
from app.graph.state import ProjectState
from app.services.test_validation import evaluate_quality_gate
from app.workspace.test_documents import write_test_report_json
from app.workspace.task_documents import write_repair_task_plan_json


def _build_is_clean(state: ProjectState) -> bool:
    summary = state.get("build_summary", {})
    return int(summary.get("failed", 0)) == 0 and int(summary.get("pending", 0)) == 0


def _append_check(state: ProjectState, check: dict) -> list[dict]:
    return [*state.get("test_results", []), check]


def _check(
    state: ProjectState,
    *,
    check_id: str,
    name: str,
    layer: str,
    language: str | None,
    command: str | None,
) -> dict:
    passed = _build_is_clean(state)
    return {
        "test_results": _append_check(
            state,
            {
                "id": check_id,
                "name": name,
                "layer": layer,
                "language": language,
                "passed": passed,
                "command": command,
                "evidence": (
                    f"Demo {layer} check passed because build summary has no failed/pending tasks."
                    if passed
                    else f"Build summary is not clean for {layer} check: {state.get('build_summary', {})}"
                ),
            },
        ),
        "test_events": [check_id],
    }


def _checks(state: ProjectState, specs: list[dict]) -> dict:
    result_state: ProjectState = state
    events = []
    for spec in specs:
        check_result = _check(result_state, **spec)
        result_state = {
            **result_state,
            "test_results": check_result["test_results"],
        }
        events.extend(check_result["test_events"])
    return {
        "test_results": result_state.get("test_results", []),
        "test_events": events,
    }


def frontend_checks(state: ProjectState) -> dict:
    return _checks(
        state,
        [
            {
                "check_id": "frontend_install",
                "name": "前端依赖安装检查",
                "layer": "frontend",
                "language": "typescript",
                "command": "npm install",
            },
            {
                "check_id": "frontend_build",
                "name": "前端 TS 构建检查",
                "layer": "frontend",
                "language": "typescript",
                "command": "npm run build",
            },
            {
                "check_id": "frontend_lint",
                "name": "前端 lint 通过",
                "layer": "frontend",
                "language": "typescript",
                "command": "npm run lint",
            },
            {
                "check_id": "frontend_typecheck",
                "name": "前端 typecheck 通过",
                "layer": "frontend",
                "language": "typescript",
                "command": "npm run typecheck",
            },
            {
                "check_id": "frontend_unit_tests",
                "name": "前端单元测试通过",
                "layer": "frontend",
                "language": "typescript",
                "command": "npm test",
            },
        ],
    )


def backend_checks(state: ProjectState) -> dict:
    return _checks(
        state,
        [
            {
                "check_id": "backend_build",
                "name": "后端 Java 构建检查",
                "layer": "backend",
                "language": "java",
                "command": "./mvnw test -DskipTests",
            },
            {
                "check_id": "backend_static_check",
                "name": "后端静态检查通过",
                "layer": "backend",
                "language": "java",
                "command": "./mvnw checkstyle:check",
            },
            {
                "check_id": "backend_unit_tests",
                "name": "后端单元测试通过",
                "layer": "backend",
                "language": "java",
                "command": "./mvnw test",
            },
        ],
    )


def api_contract_check(state: ProjectState) -> dict:
    return _check(
        state,
        check_id="api_contract",
        name="API 契约有效",
        layer="contract",
        language=None,
        command="contract-test",
    )


def joint_integration_check(state: ProjectState) -> dict:
    return _check(
        state,
        check_id="joint_integration",
        name="前后端集成测试通过",
        layer="joint",
        language=None,
        command="integration-test",
    )


def e2e_check(state: ProjectState) -> dict:
    return _check(
        state,
        check_id="e2e_tests",
        name="E2E 测试通过",
        layer="e2e",
        language=None,
        command="npx playwright test",
    )


def test_agent_review(state: ProjectState) -> dict:
    review = summarize_tests_with_deep_agent(
        test_results=state.get("test_results", []),
        build_results=state.get("build_results", []),
        workspace=workspace_from_state(state),
    )
    return {
        "test_agent_review": review,
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


def main_repair_planning(state: ProjectState) -> dict:
    repair_task_plan = plan_repairs_with_main_agent(
        test_report=state.get("test_report", {}),
        revision_requests=state.get("revision_requests", []),
        build_task_plan=state.get("build_task_plan"),
        workspace=workspace_from_state(state),
    )
    repair_task_plan_path = write_repair_task_plan_json(state, repair_task_plan)
    return {
        "repair_task_plan": repair_task_plan,
        "repair_task_plan_path": repair_task_plan_path,
        "repair_tasks": repair_task_plan["tasks"],
        "test_events": ["main_repair_planning"],
    }


def build_testing_subgraph():
    builder = StateGraph(ProjectState)

    builder.add_node("frontend_checks", frontend_checks)
    builder.add_node("backend_checks", backend_checks)
    builder.add_node("api_contract_check", api_contract_check)
    builder.add_node("joint_integration_check", joint_integration_check)
    builder.add_node("e2e_check", e2e_check)
    builder.add_node("test_agent_review", test_agent_review)
    builder.add_node("main_quality_gate", main_quality_gate)
    builder.add_node("main_repair_planning", main_repair_planning)

    builder.add_edge(START, "frontend_checks")
    builder.add_edge("frontend_checks", "backend_checks")
    builder.add_edge("backend_checks", "api_contract_check")
    builder.add_edge("api_contract_check", "joint_integration_check")
    builder.add_edge("joint_integration_check", "e2e_check")
    builder.add_edge("e2e_check", "test_agent_review")
    builder.add_edge("test_agent_review", "main_quality_gate")
    builder.add_edge("main_quality_gate", "main_repair_planning")
    builder.add_edge("main_repair_planning", END)

    return builder.compile()


_testing_subgraph = build_testing_subgraph()


def integration_test(state: ProjectState) -> dict:
    result = _testing_subgraph.invoke(
        {
            **state,
            "test_results": [],
            "test_events": [],
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
        "timeline": ["integration_test"],
    }
