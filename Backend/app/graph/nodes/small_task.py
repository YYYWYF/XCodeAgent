"""主工作流中的 SmallTask Agent 执行节点和人工升级边界。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from langgraph.config import get_stream_writer

from app.agents.tool_activity_stream import ToolActivityCallback
from app.graph.state import ProjectState
from app.services.small_task import (
    build_small_task_handoff,
    execute_small_task_batch,
)
from app.services.small_task_scope import (
    _task_paths,
    apply_confirmed_scope,
    select_parallel_small_task_batch,
    small_task_preflight,
    workflow_target_for_small_task,
)
from app.workspace.code_changes import merge_code_change_sets


def small_task_repair(state: ProjectState) -> dict[str, Any]:
    """执行局部修复任务，并按来源节点返回对应的复测入口。"""

    repair_return_node = str(state.get("repair_return_node") or "integration_test")
    if repair_return_node not in {"unit_test", "integration_test"}:
        repair_return_node = "integration_test"
    repair_phase = (
        "unit_test_repair" if repair_return_node == "unit_test" else "small_task_repair"
    )

    tasks = _initial_tasks(state)
    handoff = state.get("small_task_handoff")
    handoff = handoff if isinstance(handoff, dict) else {}
    submission = state.get("small_task_handoff_submission")
    if handoff and submission:
        decision = _submission_decision(submission)
        if decision == "rejected":
            return _handoff_rejected(state, tasks)
        if decision == "approved":
            if handoff.get("mode") == "small_task_scope_confirmation":
                tasks = apply_confirmed_scope(
                    tasks,
                    task_ids=_string_list(handoff.get("taskIds"), limit=100),
                    requested_paths=_string_list(handoff.get("requestedPaths"), limit=100),
                )
            else:
                target_node = workflow_target_for_small_task(handoff)
                return {
                    "phase": repair_phase,
                    "status": "in_progress",
                    "message": f"已确认升级，转入 {target_node} 节点继续处理。",
                    "small_task_handoff": {},
                    "small_task_handoff_submission": {},
                    "small_task_route": target_node,
                    "integration_next_action": target_node,
                    "clarification": {},
                    "repair_tasks": tasks,
                    "small_task_tasks": tasks,
                    "timeline": ["small_task_repair"],
                }
    elif handoff:
        return _await_handoff(state, tasks, handoff)

    working_tasks = [deepcopy(task) for task in tasks]
    all_results = [
        dict(item)
        for item in state.get("small_task_results", [])
        if isinstance(item, dict)
    ]
    all_change_sets = [
        dict(item)
        for item in state.get("small_task_code_change_sets", [])
        if isinstance(item, dict)
    ]
    dispatched = False
    for _ in range(20):
        pending_tasks = [
            task for task in working_tasks if str(task.get("status") or "pending") == "pending"
        ]
        if not pending_tasks:
            break
        batch = select_parallel_small_task_batch(
            working_tasks,
            max_concurrency=int(state.get("small_task_max_concurrency", 2) or 2),
        )
        if not batch:
            return _small_task_failure(
                state,
                working_tasks,
                all_results,
                all_change_sets,
                "仍有任务因依赖或并发边界无法调度。",
            )
        preflight = _first_small_task_preflight(batch)
        if preflight:
            handoff_payload = build_small_task_handoff(
                mode="small_task_workflow_handoff",
                reason=preflight["reason"],
                tasks=batch,
                escalation=preflight,
                target_node=workflow_target_for_small_task(preflight),
            )
            return _await_handoff(
                {
                    **state,
                    "small_task_tasks": working_tasks,
                    "small_task_results": all_results,
                    "small_task_code_change_sets": all_change_sets,
                },
                working_tasks,
                handoff_payload,
            )

        dispatched = True
        execution = execute_small_task_batch(
            state=state,
            tasks=batch,
            on_tool_activity=_small_task_tool_activity_writer(
                "unit_test_repair" if repair_return_node == "unit_test" else "small_task_repair"
            ),
            source=(
                "acceptance_adjustment"
                if isinstance(state.get("acceptance_adjustment"), dict)
                else f"{repair_return_node}.small_task"
            ),
        )
        batch_results = execution["results"]
        all_results.extend(batch_results)
        all_change_sets.extend(
            item
            for item in execution["codeChangeSets"]
            if isinstance(item, dict)
        )
        by_id = {
            str(item.get("taskId") or ""): item
            for item in batch_results
            if item.get("taskId")
        }
        for task in working_tasks:
            result = by_id.get(str(task.get("id") or ""))
            if not result:
                continue
            if result.get("status") in {"completed", "already_satisfied"}:
                task["status"] = "completed"
            elif result.get("status") in {
                "requires_user_confirmation",
                "requires_workflow",
            }:
                task["status"] = "pending"
            else:
                task["status"] = "failed"
            task["small_task_result"] = result

        escalation_result = next(
            (
                item
                for item in batch_results
                if item.get("status")
                in {"requires_user_confirmation", "requires_workflow"}
            ),
            None,
        )
        if escalation_result:
            escalation = escalation_result.get("escalation") or {}
            mode = (
                "small_task_scope_confirmation"
                if escalation_result.get("status") == "requires_user_confirmation"
                else "small_task_workflow_handoff"
            )
            target_node = workflow_target_for_small_task(escalation)
            handoff_payload = build_small_task_handoff(
                mode=mode,
                reason=(
                    str(escalation.get("reason") or "").strip()
                    or str(escalation_result.get("summary") or "")
                ),
                tasks=[
                    task
                    for task in working_tasks
                    if str(task.get("id") or "") == str(escalation_result.get("taskId") or "")
                ],
                escalation=escalation,
                target_node=target_node,
            )
            return _await_handoff(
                {
                    **state,
                    "small_task_tasks": working_tasks,
                    "small_task_results": all_results,
                    "small_task_code_change_sets": all_change_sets,
                },
                working_tasks,
                handoff_payload,
            )
        failed_result = next(
            (item for item in batch_results if item.get("status") == "failed"),
            None,
        )
        if failed_result:
            return _small_task_failure(
                state,
                working_tasks,
                all_results,
                all_change_sets,
                str(failed_result.get("failureReason") or failed_result.get("summary") or "小任务执行失败"),
            )

    if any(str(task.get("status") or "pending") == "pending" for task in working_tasks):
        return _small_task_failure(
            state,
            working_tasks,
            all_results,
            all_change_sets,
            "小任务执行批次超过安全上限，已停止继续修改。",
        )
    merged_changes = merge_code_change_sets(all_change_sets)
    iteration_key = (
        "unit_test_repair_iteration"
        if repair_return_node == "unit_test"
        else "repair_iteration"
    )
    next_iteration = int(state.get(iteration_key, 0) or 0) + (1 if dispatched else 0)
    next_node_label = "单元测试" if repair_return_node == "unit_test" else "集成测试"
    return {
        "phase": repair_phase,
        "status": "in_progress",
        "message": f"SmallTask Agent 已完成 {len(working_tasks)} 个局部任务，返回{next_node_label}复核。",
        "repair_tasks": working_tasks,
        "small_task_tasks": working_tasks,
        "small_task_results": all_results,
        "small_task_code_change_sets": all_change_sets,
        "small_task_handoff": {},
        "small_task_handoff_submission": {},
        "small_task_route": repair_return_node,
        "repair_return_node": repair_return_node,
        "integration_next_action": (
            repair_return_node if repair_return_node == "integration_test" else state.get("integration_next_action", "")
        ),
        "unit_test_next_action": (
            repair_return_node if repair_return_node == "unit_test" else state.get("unit_test_next_action", "")
        ),
        "acceptance_adjustment": {},
        iteration_key: next_iteration,
        "code_changes": merged_changes or state.get("code_changes", {}),
        "clarification": {},
        "timeline": ["small_task_repair"],
    }


def _initial_tasks(state: ProjectState) -> list[dict[str, Any]]:
    """读取恢复态或集成测试刚生成的小任务列表。"""

    # 验收局部修改必须优先创建本轮新任务，不能误执行上一次已经完成的修复任务。
    adjustment = state.get("acceptance_adjustment")
    if isinstance(adjustment, dict) and adjustment.get("type") == "local_fix":
        feedback = str(adjustment.get("feedback") or "").strip()
        paths = _acceptance_adjustment_paths(state)
        return [
            {
                "id": "acceptance-local-fix",
                "kind": "acceptance_local_fix",
                "owner": _acceptance_adjustment_owner(state),
                "title": "处理用户验收反馈",
                "description": feedback,
                "allowed_paths": paths,
                "target_files": paths,
                "engineering_acceptance_checks": [
                    {
                        "id": "acceptance-local-fix:feedback",
                        "kind": "user_feedback",
                        "description": feedback,
                        "required": True,
                        "target_paths": paths,
                        "verification_stage": "integration_test",
                    },
                ],
                "business_acceptance_checks": [],
                "dependencies": [],
                "status": "pending",
            }
        ]

    candidates = state.get("small_task_tasks") or state.get("repair_tasks") or []
    tasks = [deepcopy(task) for task in candidates if isinstance(task, dict)]
    if tasks:
        return tasks
    return []


def _acceptance_adjustment_paths(state: ProjectState) -> list[str]:
    """从当前页面已确认构建任务提取局部修改的精确文件范围。"""

    candidates = state.get("tasks") or []
    if not isinstance(candidates, list):
        candidates = []
    paths: list[str] = []
    for task in candidates:
        if not isinstance(task, dict):
            continue
        paths.extend(_task_paths(task))
    return list(dict.fromkeys(paths))[:100]


def _acceptance_adjustment_owner(state: ProjectState) -> str:
    """按当前执行范围决定局部验收修复的 Agent owner，避免接口修复误投前端 Agent。"""

    scope = state.get("build_execution_scope")
    scope_type = str(scope.get("type") or "").strip() if isinstance(scope, dict) else ""
    if scope_type in {"endpoint", "data_source"}:
        return "backend"
    if scope_type == "page":
        return "frontend"
    return "backend" if state.get("editor_mode") == "backend" else "frontend"


def _await_handoff(
    state: dict[str, Any],
    tasks: list[dict[str, Any]],
    handoff: dict[str, Any],
) -> dict[str, Any]:
    """保存待确认升级状态，并让主工作流在当前节点停止。"""

    return {
        "phase": (
            "unit_test_repair"
            if state.get("repair_return_node") == "unit_test"
            else "small_task_repair"
        ),
        "status": "requires_user_input",
        "message": str(handoff.get("message") or "SmallTask Agent 需要你的确认。"),
        "repair_tasks": tasks,
        "small_task_tasks": tasks,
        "small_task_results": state.get("small_task_results", []),
        "small_task_code_change_sets": state.get("small_task_code_change_sets", []),
        "small_task_handoff": handoff,
        "small_task_handoff_submission": {},
        "small_task_route": "await_user_input",
        "repair_return_node": state.get("repair_return_node", "integration_test"),
        "integration_next_action": (
            "await_user_input"
            if state.get("repair_return_node", "integration_test") == "integration_test"
            else state.get("integration_next_action", "await_user_input")
        ),
        "unit_test_next_action": (
            "await_user_input"
            if state.get("repair_return_node", "integration_test") == "unit_test"
            else state.get("unit_test_next_action", "await_user_input")
        ),
        "clarification": handoff,
        "timeline": ["small_task_repair"],
    }


def _handoff_rejected(state: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """把用户拒绝范围或正式升级转换为可解释的终止失败。"""

    return _small_task_failure(
        state,
        tasks,
        [dict(item) for item in state.get("small_task_results", []) if isinstance(item, dict)],
        [dict(item) for item in state.get("small_task_code_change_sets", []) if isinstance(item, dict)],
        "用户未批准 SmallTask Agent 的升级范围，本轮修改已停止。",
    )


def _small_task_failure(
    state: dict[str, Any],
    tasks: list[dict[str, Any]],
    results: list[dict[str, Any]],
    change_sets: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    """生成失败路由需要的完整小任务状态和有界代码差异。"""

    return {
        "phase": (
            "unit_test_repair"
            if state.get("repair_return_node") == "unit_test"
            else "small_task_repair"
        ),
        "status": "failed",
        "message": reason[:2_000],
        "repair_tasks": tasks,
        "small_task_tasks": tasks,
        "small_task_results": results,
        "small_task_code_change_sets": change_sets,
        "small_task_handoff": {},
        "small_task_handoff_submission": {},
        "small_task_route": "handle_failure",
        "repair_return_node": state.get("repair_return_node", "integration_test"),
        "integration_next_action": (
            "handle_failure"
            if state.get("repair_return_node", "integration_test") == "integration_test"
            else state.get("integration_next_action", "handle_failure")
        ),
        "unit_test_next_action": (
            "handle_failure"
            if state.get("repair_return_node", "integration_test") == "unit_test"
            else state.get("unit_test_next_action", "handle_failure")
        ),
        "code_changes": merge_code_change_sets(change_sets) or state.get("code_changes", {}),
        "clarification": {},
        "timeline": ["small_task_repair"],
    }


def _await_handoff_submission_text(submission: Any) -> str:
    """兼容结构化提交和旧文本恢复态中的用户决定。"""

    if isinstance(submission, dict):
        return str(submission.get("decision") or submission.get("value") or "").strip().lower()
    return str(submission or "").strip().lower()


def _submission_decision(submission: Any) -> str:
    """把前端提交规范化为 approved 或 rejected。"""

    value = _await_handoff_submission_text(submission)
    if value in {"approved", "approve", "yes", "是", "同意", "确认", "批准"}:
        return "approved"
    if value in {"rejected", "reject", "no", "否", "拒绝", "不同意"}:
        return "rejected"
    return ""


def _small_task_tool_activity_writer(node_name: str = "small_task_repair") -> ToolActivityCallback | None:
    """把并行 Agent 工具活动发布到主工作流的 custom stream。"""

    try:
        writer = get_stream_writer()
    except RuntimeError:
        return None

    def report(activity: dict[str, Any]) -> None:
        """发送一次带 SmallTask 节点归属的工具活动。"""

        writer(
            {
                "type": "small_task.tool_activity",
                "node_name": node_name,
                "activity": activity,
            }
        )

    return report


def unit_test_repair(state: ProjectState) -> dict[str, Any]:
    """执行开发阶段单元测试失败后的 SmallTask 修复。"""

    return small_task_repair({**state, "repair_return_node": "unit_test"})


def _first_small_task_preflight(tasks: list[dict[str, Any]]) -> dict[str, str]:
    """返回当前批次第一个需要升级的任务，避免重复执行前置判定。"""

    for task in tasks:
        result = small_task_preflight(task)
        if result:
            return result
    return {}


def _string_list(value: Any, *, limit: int) -> list[str]:
    """从不可信值提取有界字符串列表。"""

    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]
