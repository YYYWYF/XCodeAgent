"""开发阶段单元测试生成、执行和 SmallTask 修复子图。"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.graph.state import ProjectState
from app.graph.subgraphs.testing import (
    INTEGRATION_TEST_PROGRESS_REPORTER_KEY,
    _check_progress_snapshot_writer,
    _route_unit_test_confirmation,
    actual_project_checks,
    collect_unit_test_targets,
    generate_unit_tests,
    repair_planning,
    skip_unit_tests,
    unit_test_confirmation,
    validate_generated_unit_tests,
)
from app.services.test_validation import evaluate_quality_gate
from app.workspace.code_changes import merge_code_change_sets
from app.workspace.spec_documents import workflow_artifact_root
from app.workspace.test_documents import write_test_report_json


def unit_test_quality_gate(state: ProjectState) -> dict[str, Any]:
    """只评估开发阶段单元测试结果，避免把单测混入测试阶段质量报告。"""

    report = evaluate_quality_gate(
        test_results=state.get("test_results", []),
        source="unit_test",
    )
    report_path = str(
        state.get("unit_test_report_path")
        or workflow_artifact_root(state) / "reports" / "unit-test-report.json"
    )
    report_path = write_test_report_json(
        {**state, "test_report_json_path": report_path}, report
    )
    passed = bool(report.get("passed"))
    return {
        "phase": "unit_test",
        "test_report": report,
        "test_report_json_path": report_path,
        "unit_test_report": report,
        "unit_test_report_path": report_path,
        "unit_test_quality_gate_passed": passed,
        "quality_gate_passed": passed,
        "needs_revision": report.get("needs_revision", not passed),
        "revision_requests": report.get("revision_requests", []),
        "test_events": ["unit_test_quality_gate"],
    }


def _unit_repair_state(state: ProjectState) -> dict[str, Any]:
    """把单元测试独立状态映射到既有 RepairPlanner 输入契约。"""

    repair_plan_path = str(
        state.get("unit_test_repair_task_plan_path")
        or workflow_artifact_root(state) / "plans" / "unit-test-repair-task-plan.json"
    )
    return {
        **state,
        "test_results": state.get("test_results", []),
        "test_report": state.get("test_report", {}),
        "test_report_json_path": state.get("unit_test_report_path"),
        "quality_gate_passed": state.get("unit_test_quality_gate_passed", False),
        "needs_revision": state.get("needs_revision", False),
        "revision_requests": state.get("revision_requests", []),
        "repair_task_plan": state.get("unit_test_repair_task_plan", {}),
        "repair_task_plan_path": repair_plan_path,
        "repair_iteration": int(state.get("unit_test_repair_iteration", 0) or 0),
        "max_repair_iterations": int(
            state.get("unit_test_max_repair_iterations", 3) or 3
        ),
        "integration_repair_enabled": state.get("unit_test_repair_enabled", True),
        "integration_next_action": state.get("unit_test_next_action", ""),
        "repair_return_node": "unit_test",
    }


def unit_repair_planning(state: ProjectState) -> dict[str, Any]:
    """为单元测试失败创建有界修复计划，并转换回开发阶段状态字段。"""

    result = repair_planning(_unit_repair_state(state))
    generic_next = str(result.get("integration_next_action") or "handle_failure")
    if generic_next == "small_task_repair":
        next_action = "unit_test_repair"
    elif generic_next in {"launch_project", "review_phase_confirmation"}:
        next_action = "test_phase_confirmation"
    else:
        next_action = generic_next
    # RepairPlanner 的范围确认载荷本身就是一个待交互边界；显式标记状态，
    # 避免恢复态继承上一轮 failed/in_progress 而被误投影为终态失败。
    status = (
        "requires_user_input"
        if generic_next == "await_user_input"
        else result.get("status")
    )
    return {
        **result,
        "phase": "unit_test",
        "status": status,
        "unit_test_repair_task_plan": result.get("repair_task_plan", {}),
        "unit_test_repair_task_plan_path": result.get("repair_task_plan_path"),
        "unit_test_repair_iteration": result.get(
            "repair_iteration", state.get("unit_test_repair_iteration", 0)
        ),
        "unit_test_max_repair_iterations": result.get(
            "max_repair_iterations", state.get("unit_test_max_repair_iterations", 3)
        ),
        "unit_test_next_action": next_action,
        "unit_test_gate_passed": bool(
            state.get("unit_test_quality_gate_passed", False)
        ),
        "repair_return_node": "unit_test",
        "small_task_route": "unit_test_repair"
        if next_action == "unit_test_repair"
        else generic_next,
        "integration_next_action": generic_next,
        "test_results": state.get("test_results", []),
        "unit_test_results": state.get("test_results", []),
    }


def build_unit_testing_subgraph():
    """构建开发阶段的单元测试确认、生成、执行和修复规划流程。"""

    builder = StateGraph(ProjectState)
    builder.add_node("collect_unit_test_targets", collect_unit_test_targets)
    builder.add_node("unit_test_confirmation", unit_test_confirmation)
    builder.add_node("skip_unit_tests", skip_unit_tests)
    builder.add_node("generate_unit_tests", generate_unit_tests)
    builder.add_node("validate_generated_unit_tests", validate_generated_unit_tests)
    builder.add_node("actual_project_checks", actual_project_checks)
    builder.add_node("unit_test_quality_gate", unit_test_quality_gate)
    builder.add_node("unit_repair_planning", unit_repair_planning)

    builder.add_edge(START, "collect_unit_test_targets")
    builder.add_edge("collect_unit_test_targets", "unit_test_confirmation")
    builder.add_conditional_edges(
        "unit_test_confirmation",
        _route_unit_test_confirmation,
        {
            "skip_unit_tests": "skip_unit_tests",
            "generate_unit_tests": "generate_unit_tests",
            "await_user_input": END,
        },
    )
    builder.add_edge("skip_unit_tests", "unit_test_quality_gate")
    builder.add_edge("generate_unit_tests", "validate_generated_unit_tests")
    builder.add_edge("validate_generated_unit_tests", "actual_project_checks")
    builder.add_edge("actual_project_checks", "unit_test_quality_gate")
    builder.add_edge("unit_test_quality_gate", "unit_repair_planning")
    builder.add_edge("unit_repair_planning", END)
    return builder.compile()


_unit_testing_subgraph = build_unit_testing_subgraph()


def _stable_unit_test_build_diff(state: ProjectState) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """首次进入单测节点时捕获 Build Diff，后续修复重试始终复用该快照。"""

    existing_changes = state.get("unit_test_build_code_changes")
    if isinstance(existing_changes, dict) and state.get("unit_test_build_diff_captured"):
        existing_sets = [
            item
            for item in state.get("unit_test_build_code_change_sets", [])
            if isinstance(item, dict)
        ]
        return existing_changes, existing_sets
    code_changes = state.get("code_changes")
    stable_changes = code_changes if isinstance(code_changes, dict) else {}
    stable_sets = [
        item for item in state.get("code_change_sets", []) if isinstance(item, dict)
    ]
    return stable_changes, stable_sets


def _unique_change_sets(change_sets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按稳定 ID 或内容签名去重代码变更集，避免 Build Diff 在公开投影中重复。"""

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for change_set in change_sets:
        if not isinstance(change_set, dict):
            continue
        identifier = _change_set_identity(change_set)
        if identifier in seen:
            continue
        seen.add(identifier)
        unique.append(change_set)
    return unique


def _change_set_identity(change_set: dict[str, Any]) -> str:
    """返回用于判断全局变更集合是否已写入的稳定签名。"""

    identifier = str(change_set.get("id") or "").strip()
    if identifier:
        return identifier
    files = change_set.get("files")
    return repr(files) if isinstance(files, list) else repr(change_set)


def unit_test(state: ProjectState) -> dict[str, Any]:
    """运行开发阶段单元测试子图，并输出独立门禁与修复状态。"""

    stable_changes, stable_change_sets = _stable_unit_test_build_diff(state)
    previous_test_changes = [
        item
        for item in (
            state.get("unit_test_generation_code_change_sets")
            or state.get("unit_test_code_change_sets", [])
        )
        if isinstance(item, dict)
    ]
    internal_state = {
        **state,
        "test_generation_input_code_changes": stable_changes,
        "test_generation_input_code_change_sets": stable_change_sets,
        "unit_test_build_code_changes": stable_changes,
        "unit_test_build_code_change_sets": stable_change_sets,
        "unit_test_build_diff_captured": True,
        "unit_test_code_change_sets": previous_test_changes,
        "unit_test_generation_code_change_sets": previous_test_changes,
        "test_results": [],
        "test_report": {},
        "test_report_path": None,
        "test_report_json_path": None,
        "quality_gate_passed": False,
        "needs_revision": False,
        "revision_requests": [],
        "repair_task_plan": state.get("unit_test_repair_task_plan", {}),
        "repair_task_plan_path": state.get("unit_test_repair_task_plan_path"),
        "repair_tasks": state.get("repair_tasks", []),
        "repair_iteration": int(state.get("unit_test_repair_iteration", 0) or 0),
        "max_repair_iterations": int(
            state.get("unit_test_max_repair_iterations", 3) or 3
        ),
        "integration_repair_enabled": state.get("unit_test_repair_enabled", True),
        "integration_next_action": "",
        "clarification": {},
        "test_events": [],
        "code_changes": {},
        "code_change_sets": [],
        "timeline": [],
    }
    result = _unit_testing_subgraph.invoke(
        internal_state,
        config={
            "configurable": {
                INTEGRATION_TEST_PROGRESS_REPORTER_KEY: _check_progress_snapshot_writer(
                    "unit_test.checks"
                ),
            }
        },
    )
    clarification = result.get("clarification")
    clarification = clarification if isinstance(clarification, dict) else {}
    waiting = (
        result.get("status") == "requires_user_input"
        and result.get("integration_next_action") == "await_user_input"
    )
    quality_passed = bool(result.get("unit_test_quality_gate_passed"))
    next_action = str(result.get("unit_test_next_action") or "")
    if waiting:
        status = "requires_user_input"
    elif next_action == "unit_test_repair":
        status = "in_progress"
    elif quality_passed:
        status = "completed"
        next_action = "test_phase_confirmation"
    else:
        status = "failed"
    current_test_changes = [
        item
        for item in (
            result.get("unit_test_generation_code_change_sets")
            or result.get("unit_test_code_change_sets", [])
        )
        if isinstance(item, dict)
    ]
    repair_changes = [
        item for item in result.get("code_change_sets", []) if isinstance(item, dict)
    ]
    prior_small_task_changes = [
        item
        for item in state.get("small_task_code_change_sets", [])
        if isinstance(item, dict)
    ]
    prior_merged_changes = state.get("code_changes")
    prior_merged_changes = (
        prior_merged_changes
        if isinstance(prior_merged_changes, dict) and prior_merged_changes.get("files")
        else {}
    )
    stable_inputs = _unique_change_sets(
        [
            *(
                [stable_changes]
                if isinstance(stable_changes, dict) and stable_changes.get("files")
                else []
            ),
            *stable_change_sets,
        ]
    )
    # 恢复态可能只携带已合并的公开 Diff；仅在没有更细粒度的生成/修复集合时
    # 采用它，避免把同一批文件同时按 Build 集合和合并集合重复投影。
    resumed_merged_only = bool(
        prior_merged_changes
        and not current_test_changes
        and not repair_changes
        and not prior_small_task_changes
    )
    base_change_sets = [prior_merged_changes] if resumed_merged_only else stable_inputs
    all_change_sets = _unique_change_sets(
        [
            *base_change_sets,
            *current_test_changes,
            *repair_changes,
            *prior_small_task_changes,
        ]
    )
    existing_global_change_sets = {
        _change_set_identity(item)
        for item in state.get("code_change_sets", [])
        if isinstance(item, dict)
    }
    # `code_change_sets` 使用 add reducer；节点只返回本轮新增集合，避免在单测重试
    # 时再次把首次 Build Diff 追加到全局列表。完整开发 Diff 仍由 code_changes 返回。
    new_global_change_sets = [
        item
        for item in all_change_sets
        if _change_set_identity(item) not in existing_global_change_sets
    ]
    all_changes = merge_code_change_sets(all_change_sets) or stable_changes
    return {
        "phase": "unit_test",
        "status": status,
        "clarification": clarification if waiting else {},
        "unit_test_quality_gate_passed": quality_passed,
        "unit_test_gate_passed": quality_passed,
        "unit_test_next_action": (
            next_action
            or ("await_user_input" if waiting else "")
            or result.get("integration_next_action", "handle_failure")
        ),
        "unit_test_results": result.get("test_results", []),
        "unit_test_report": result.get("unit_test_report") or result.get("test_report", {}),
        "unit_test_report_path": result.get("unit_test_report_path")
        or result.get("test_report_json_path"),
        "unit_test_generation_context": result.get("unit_test_generation_context", {}),
        "unit_test_generation": result.get("unit_test_generation", {}),
        "unit_test_affected_layers": result.get("unit_test_affected_layers", []),
        "unit_test_mapping_path": result.get("unit_test_mapping_path")
        or (
            result.get("unit_test_generation", {}).get("mapping_path")
            if isinstance(result.get("unit_test_generation"), dict)
            else None
        ),
        "unit_test_decision": result.get("unit_test_decision", state.get("unit_test_decision", "")),
        "unit_test_build_code_changes": stable_changes,
        "unit_test_build_code_change_sets": stable_change_sets,
        "unit_test_build_diff_captured": True,
        "unit_test_code_change_sets": current_test_changes,
        "unit_test_generation_code_change_sets": current_test_changes,
        "unit_test_repair_task_plan": result.get("unit_test_repair_task_plan", {}),
        "unit_test_repair_task_plan_path": result.get("unit_test_repair_task_plan_path"),
        "unit_test_repair_iteration": result.get(
            "unit_test_repair_iteration", state.get("unit_test_repair_iteration", 0)
        ),
        "unit_test_max_repair_iterations": result.get(
            "unit_test_max_repair_iterations", state.get("unit_test_max_repair_iterations", 3)
        ),
        "repair_tasks": result.get("repair_tasks", []),
        "small_task_tasks": result.get("repair_tasks", []),
        "small_task_results": state.get("small_task_results", []),
        "small_task_code_change_sets": [
            *prior_small_task_changes,
            *repair_changes,
        ],
        "small_task_handoff": result.get(
            "small_task_handoff", state.get("small_task_handoff", {})
        ),
        "small_task_handoff_submission": state.get(
            "small_task_handoff_submission", {}
        ),
        "small_task_route": "unit_test_repair"
        if next_action == "unit_test_repair"
        else result.get("small_task_route", next_action),
        "repair_return_node": "unit_test",
        "code_changes": all_changes,
        "code_change_sets": new_global_change_sets,
        "test_events": result.get("test_events", []),
        "timeline": ["unit_test"],
    }
