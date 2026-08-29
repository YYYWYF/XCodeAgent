"""自由对话测试失败后的有界自动修复节点。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from langgraph.config import get_stream_writer

from app.agents.repair_planner import plan_repairs_with_repair_planner_agent
from app.agents.tool_activity_stream import ToolActivityCallback
from app.graph.nodes.common import capture_agent_file_changes, workspace_from_state
from app.graph.state import ProjectState
from app.services.small_task import (
    build_small_task_handoff,
    execute_small_task_batch,
)
from app.services.direct_repair import (
    authorized_direct_repair_paths,
    candidate_repair_tasks,
    direct_repair_dict_list,
    direct_repair_plan_has_files,
    first_direct_repair_preflight,
    normalize_direct_repair_tasks,
    normalize_direct_revision_requests,
    persist_direct_repair_plan,
    repair_plan_handoff,
    scoped_direct_repair_tasks,
    terminal_repair_plan,
)
from app.services.small_task_scope import (
    select_parallel_small_task_batch,
    workflow_target_for_small_task,
)
from app.services.revision_routing import build_small_task_revision_confirmation
from app.workspace.code_changes import merge_code_change_sets


DIRECT_MAX_REPAIR_ITERATIONS = 3


def direct_modification_repair(state: ProjectState) -> dict[str, Any]:
    """根据自由对话测试证据执行一轮精确路径修复，并返回集成测试。"""

    iteration = max(0, int(state.get("repair_iteration", 0) or 0))
    maximum = max(
        1,
        int(
            state.get("max_repair_iterations", DIRECT_MAX_REPAIR_ITERATIONS)
            or DIRECT_MAX_REPAIR_ITERATIONS
        ),
    )
    previous_direct_changes = direct_repair_dict_list(state.get("direct_code_change_sets"))
    previous_small_task_results = direct_repair_dict_list(state.get("small_task_results"))
    previous_small_task_changes = direct_repair_dict_list(
        state.get("small_task_code_change_sets")
    )

    if iteration >= maximum:
        plan = terminal_repair_plan(
            reason=f"自由对话自动修复已达到 {maximum} 轮上限。"
        )
        plan_path = persist_direct_repair_plan(state, plan)
        return _repair_failure(
            state,
            iteration=iteration,
            maximum=maximum,
            reason=f"快速修改验证失败，自动修复已达到 {maximum} 轮上限，已停止继续修改。",
            plan=plan,
            plan_path=plan_path,
            tasks=[],
            results=previous_small_task_results,
            small_task_changes=previous_small_task_changes,
            direct_changes=previous_direct_changes,
        )

    revision_requests = normalize_direct_revision_requests(state.get("revision_requests"))
    authorized_paths = authorized_direct_repair_paths(state)
    if not revision_requests:
        return _repair_failure(
            state,
            iteration=iteration,
            maximum=maximum,
            reason="测试失败没有提供可定位的返修证据，已停止自动修复。",
            plan={},
            plan_path="",
            tasks=[],
            results=previous_small_task_results,
            small_task_changes=previous_small_task_changes,
            direct_changes=previous_direct_changes,
        )
    if not any(authorized_paths.values()):
        return _repair_failure(
            state,
            iteration=iteration,
            maximum=maximum,
            reason="测试失败没有对应的实际代码差异路径，无法安全授权自动修复。",
            plan={},
            plan_path="",
            tasks=[],
            results=previous_small_task_results,
            small_task_changes=previous_small_task_changes,
            direct_changes=previous_direct_changes,
        )

    scoped_tasks = scoped_direct_repair_tasks(authorized_paths)
    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="conversation.direct_modification_repair_planner",
        action=lambda: plan_repairs_with_repair_planner_agent(
            test_report=state.get("test_report", {}),
            revision_requests=revision_requests,
            build_task_plan=None,
            build_execution_scope={"type": "application", "targetId": "conversation"},
            scoped_tasks=scoped_tasks,
            repair_attempt=iteration + 1,
            workspace=workspace,
            selected_skill_names=state.get("selected_skill_names"),
        ),
        capture_exceptions=True,
    )
    if captured.error is not None:
        return _repair_failure(
            state,
            iteration=iteration,
            maximum=maximum,
            reason=f"RepairPlanner 执行失败：{type(captured.error).__name__}: {captured.error}",
            plan={},
            plan_path="",
            tasks=[],
            results=previous_small_task_results,
            small_task_changes=previous_small_task_changes,
            direct_changes=previous_direct_changes,
        )
    if direct_repair_plan_has_files(captured.code_change_set):
        return _repair_failure(
            state,
            iteration=iteration,
            maximum=maximum,
            reason="RepairPlanner 违反只读边界并修改了工作区，已停止自动修复。",
            plan=captured.value if isinstance(captured.value, dict) else {},
            plan_path="",
            tasks=[],
            results=previous_small_task_results,
            small_task_changes=previous_small_task_changes,
            direct_changes=previous_direct_changes,
        )

    plan = captured.value if isinstance(captured.value, dict) else {}
    plan_path = persist_direct_repair_plan(state, plan)
    if plan.get("decision") == "requires_user_confirmation" or plan.get("status") == "requires_user_confirmation":
        if plan.get("escalationKind") == "formal_revision":
            reason = str(plan.get("reason") or "自动修复需要改变已确认的正式语义。")
            revision_confirmation = build_small_task_revision_confirmation(
                state=state,
                escalation={
                    "reasonCode": "formal_revision",
                    "reason": reason,
                    "requestedPaths": [],
                    "requestedResources": plan.get("requestedResources", []),
                },
                reason=reason,
            )
            return {
                **_repair_wait(
                    state,
                    iteration=iteration,
                    maximum=maximum,
                    message="自动修复明确需要改变正式语义，已暂停写入。",
                    plan=plan,
                    plan_path=plan_path,
                    tasks=candidate_repair_tasks(plan),
                    results=previous_small_task_results,
                    small_task_changes=previous_small_task_changes,
                    direct_changes=previous_direct_changes,
                    handoff={},
                ),
                **revision_confirmation,
                "small_task_handoff": {},
            }
        requested_paths = plan.get("requestedPaths")
        if not isinstance(requested_paths, list) or not any(
            str(path).strip() for path in requested_paths
        ):
            return _repair_failure(
                state,
                iteration=iteration,
                maximum=maximum,
                reason="RepairPlanner 没有提供可验证的真实代码文件范围，已停止自动修复。",
                plan=plan,
                plan_path=plan_path,
                tasks=candidate_repair_tasks(plan),
                results=previous_small_task_results,
                small_task_changes=previous_small_task_changes,
                direct_changes=previous_direct_changes,
            )
        handoff = repair_plan_handoff(plan)
        return _repair_wait(
            state,
            iteration=iteration,
            maximum=maximum,
            message="自动修复计划需要确认修改范围，已暂停继续写入。",
            plan=plan,
            plan_path=plan_path,
            tasks=candidate_repair_tasks(plan),
            results=previous_small_task_results,
            small_task_changes=previous_small_task_changes,
            direct_changes=previous_direct_changes,
            handoff=handoff,
        )
    if plan.get("decision") == "terminal_failure" or plan.get("status") == "terminal_failure":
        return _repair_failure(
            state,
            iteration=iteration,
            maximum=maximum,
            reason=str(plan.get("reason") or "RepairPlanner 判定当前失败无法安全自动修复。"),
            plan=plan,
            plan_path=plan_path,
            tasks=[],
            results=previous_small_task_results,
            small_task_changes=previous_small_task_changes,
            direct_changes=previous_direct_changes,
        )

    tasks, scope_error = normalize_direct_repair_tasks(
        plan.get("tasks"),
        authorized_paths=authorized_paths,
    )
    if scope_error:
        return _repair_failure(
            state,
            iteration=iteration,
            maximum=maximum,
            reason=scope_error,
            plan=plan,
            plan_path=plan_path,
            tasks=tasks,
            results=previous_small_task_results,
            small_task_changes=previous_small_task_changes,
            direct_changes=previous_direct_changes,
        )
    if not tasks:
        return _repair_failure(
            state,
            iteration=iteration,
            maximum=maximum,
            reason="RepairPlanner 没有生成可执行的局部修复任务。",
            plan=plan,
            plan_path=plan_path,
            tasks=[],
            results=previous_small_task_results,
            small_task_changes=previous_small_task_changes,
            direct_changes=previous_direct_changes,
        )

    preflight = first_direct_repair_preflight(tasks)
    if preflight:
        if preflight.get("reasonCode") == "missing_code_scope":
            return _repair_failure(
                state,
                iteration=iteration,
                maximum=maximum,
                reason=str(preflight.get("reason") or "自动修复缺少真实代码文件范围。"),
                plan=plan,
                plan_path=plan_path,
                tasks=tasks,
                results=previous_small_task_results,
                small_task_changes=previous_small_task_changes,
                direct_changes=previous_direct_changes,
            )
        revision_confirmation = build_small_task_revision_confirmation(
            state=state,
            escalation=preflight,
            reason=str(preflight.get("reason") or "自动修复需要正式修改。"),
        )
        return {
            **_repair_wait(
            state,
            iteration=iteration,
            maximum=maximum,
            message="自动修复触及正式语义，已暂停并等待影响范围确认。",
            plan=plan,
            plan_path=plan_path,
            tasks=tasks,
            results=previous_small_task_results,
            small_task_changes=previous_small_task_changes,
            direct_changes=previous_direct_changes,
            handoff={},
            ),
            **revision_confirmation,
            "small_task_handoff": {},
        }

    working_tasks = [deepcopy(task) for task in tasks]
    all_results = [*previous_small_task_results]
    all_small_task_changes = [*previous_small_task_changes]
    new_direct_changes: list[dict[str, Any]] = []
    execution_state = {
        **state,
        "revision_requests": revision_requests,
        "build_execution_scope": {"type": "application", "targetId": "conversation"},
    }
    dispatched = False
    for _ in range(20):
        pending = [
            task
            for task in working_tasks
            if str(task.get("status") or "pending") == "pending"
        ]
        if not pending:
            break
        batch = select_parallel_small_task_batch(
            working_tasks,
            max_concurrency=int(state.get("small_task_max_concurrency", 2) or 2),
        )
        if not batch:
            return _repair_failure(
                state,
                iteration=iteration,
                maximum=maximum,
                reason="自动修复任务因依赖或并发边界无法调度。",
                plan=plan,
                plan_path=plan_path,
                tasks=working_tasks,
                results=all_results,
                small_task_changes=all_small_task_changes,
                direct_changes=[*previous_direct_changes, *new_direct_changes],
            )
        dispatched = True
        execution = execute_small_task_batch(
            state=execution_state,
            tasks=batch,
            on_tool_activity=_direct_repair_tool_activity_writer(),
            source="conversation.direct_modification_repair",
        )
        batch_results = [
            item for item in execution.get("results", []) if isinstance(item, dict)
        ]
        all_results.extend(batch_results)
        batch_changes = [
            item
            for item in execution.get("codeChangeSets", [])
            if isinstance(item, dict)
        ]
        all_small_task_changes.extend(batch_changes)
        new_direct_changes.extend(batch_changes)
        by_id = {
            str(item.get("taskId") or ""): item
            for item in batch_results
            if item.get("taskId")
        }
        for task in working_tasks:
            result = by_id.get(str(task.get("id") or ""))
            if not result:
                continue
            task["small_task_result"] = result
            task["status"] = (
                "completed"
                if result.get("status") in {"completed", "already_satisfied"}
                else "pending"
                if result.get("status") in {"requires_user_confirmation", "requires_workflow"}
                else "failed"
            )

        escalation_result = next(
            (
                item
                for item in batch_results
                if item.get("status") in {"requires_user_confirmation", "requires_workflow"}
            ),
            None,
        )
        if escalation_result:
            escalation = escalation_result.get("escalation") or {}
            if escalation_result.get("status") == "requires_workflow":
                reason = (
                    str(escalation.get("reason") or "").strip()
                    or str(escalation_result.get("summary") or "")
                    or "SmallTask 自动修复需要正式修改。"
                )
                revision_confirmation = build_small_task_revision_confirmation(
                    state=state,
                    escalation=escalation,
                    reason=reason,
                )
                return {
                    **_repair_wait(
                        state,
                        iteration=iteration,
                        maximum=maximum,
                        message="SmallTask 发现正式语义变化，已暂停写入。",
                        plan=plan,
                        plan_path=plan_path,
                        tasks=working_tasks,
                        results=all_results,
                        small_task_changes=all_small_task_changes,
                        direct_changes=[*previous_direct_changes, *new_direct_changes],
                        handoff={},
                    ),
                    **revision_confirmation,
                    "small_task_handoff": {},
                }
            handoff = build_small_task_handoff(
                mode="small_task_scope_confirmation",
                reason=(
                    str(escalation.get("reason") or "").strip()
                    or str(escalation_result.get("summary") or "")
                ),
                tasks=[
                    task
                    for task in working_tasks
                    if str(task.get("id") or "")
                    == str(escalation_result.get("taskId") or "")
                ],
                escalation=escalation,
                target_node=workflow_target_for_small_task(escalation),
            )
            return _repair_wait(
                state,
                iteration=iteration,
                maximum=maximum,
                message="SmallTask Agent 请求扩大自动修复范围，已暂停写入。",
                plan=plan,
                plan_path=plan_path,
                tasks=working_tasks,
                results=all_results,
                small_task_changes=all_small_task_changes,
                direct_changes=[*previous_direct_changes, *new_direct_changes],
                handoff=handoff,
            )
        failed_result = next(
            (item for item in batch_results if item.get("status") == "failed"),
            None,
        )
        if failed_result:
            return _repair_failure(
                state,
                iteration=iteration,
                maximum=maximum,
                reason=str(
                    failed_result.get("failureReason")
                    or failed_result.get("summary")
                    or "SmallTask Agent 自动修复失败。"
                ),
                plan=plan,
                plan_path=plan_path,
                tasks=working_tasks,
                results=all_results,
                small_task_changes=all_small_task_changes,
                direct_changes=[*previous_direct_changes, *new_direct_changes],
            )
        if execution.get("unauthorizedPaths"):
            return _repair_failure(
                state,
                iteration=iteration,
                maximum=maximum,
                reason="自动修复检测到授权范围外的文件变更，已停止继续修改。",
                plan=plan,
                plan_path=plan_path,
                tasks=working_tasks,
                results=all_results,
                small_task_changes=all_small_task_changes,
                direct_changes=[*previous_direct_changes, *new_direct_changes],
            )

    if any(str(task.get("status") or "pending") == "pending" for task in working_tasks):
        return _repair_failure(
            state,
            iteration=iteration,
            maximum=maximum,
            reason="自动修复批次超过安全调度上限，已停止继续修改。",
            plan=plan,
            plan_path=plan_path,
            tasks=working_tasks,
            results=all_results,
            small_task_changes=all_small_task_changes,
            direct_changes=[*previous_direct_changes, *new_direct_changes],
        )

    next_iteration = iteration + (1 if dispatched else 0)
    direct_changes = [*previous_direct_changes, *new_direct_changes]
    return {
        "phase": "direct_modification_repair",
        "status": "in_progress",
        "message": f"自由对话自动修复第 {next_iteration}/{maximum} 轮完成，返回集成测试复核。",
        "repair_task_plan": plan,
        "repair_task_plan_path": plan_path,
        "repair_tasks": working_tasks,
        "small_task_tasks": working_tasks,
        "small_task_results": all_results,
        "small_task_code_change_sets": all_small_task_changes,
        "small_task_handoff": {},
        "small_task_handoff_submission": {},
        "small_task_route": "validate_direct_fix",
        "repair_iteration": next_iteration,
        "max_repair_iterations": maximum,
        "integration_next_action": "validate_direct_fix",
        "direct_code_change_sets": direct_changes,
        "code_changes": merge_code_change_sets(direct_changes) or state.get("code_changes", {}),
        "clarification": {},
        "timeline": ["direct_modification_repair"],
    }


def _repair_wait(
    state: ProjectState,
    *,
    iteration: int,
    maximum: int,
    message: str,
    plan: dict[str, Any],
    plan_path: str,
    tasks: list[dict[str, Any]],
    results: list[dict[str, Any]],
    small_task_changes: list[dict[str, Any]],
    direct_changes: list[dict[str, Any]],
    handoff: dict[str, Any],
) -> dict[str, Any]:
    """返回需要用户确认的修复状态，并保留当前所有证据。"""

    return {
        "phase": "direct_modification_repair",
        "status": "requires_user_input",
        "message": message,
        "repair_task_plan": plan,
        "repair_task_plan_path": plan_path,
        "repair_tasks": tasks,
        "small_task_tasks": tasks,
        "small_task_results": results,
        "small_task_code_change_sets": small_task_changes,
        "small_task_handoff": handoff,
        "small_task_handoff_submission": {},
        "small_task_route": "await_user_input",
        "repair_iteration": iteration,
        "max_repair_iterations": maximum,
        "integration_next_action": "await_user_input",
        "direct_code_change_sets": direct_changes,
        "code_changes": merge_code_change_sets(direct_changes) or state.get("code_changes", {}),
        "clarification": handoff,
        "timeline": ["direct_modification_repair"],
    }


def _repair_failure(
    state: ProjectState,
    *,
    iteration: int,
    maximum: int,
    reason: str,
    plan: dict[str, Any],
    plan_path: str,
    tasks: list[dict[str, Any]],
    results: list[dict[str, Any]],
    small_task_changes: list[dict[str, Any]],
    direct_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """生成自动修复终止状态，并避免失败时丢失已经落盘的差异。"""

    return {
        "phase": "direct_modification_repair",
        "status": "failed",
        "message": reason[:2_000],
        "repair_task_plan": plan,
        "repair_task_plan_path": plan_path,
        "repair_tasks": tasks,
        "small_task_tasks": tasks,
        "small_task_results": results,
        "small_task_code_change_sets": small_task_changes,
        "small_task_handoff": {},
        "small_task_handoff_submission": {},
        "small_task_route": "handle_failure",
        "repair_iteration": iteration,
        "max_repair_iterations": maximum,
        "integration_next_action": "handle_failure",
        "direct_code_change_sets": direct_changes,
        "code_changes": merge_code_change_sets(direct_changes) or state.get("code_changes", {}),
        "clarification": {},
        "timeline": ["direct_modification_repair"],
    }


def _direct_repair_tool_activity_writer() -> ToolActivityCallback | None:
    """把自由对话修复 Agent 的工具活动投射到 conversation custom stream。"""

    try:
        writer = get_stream_writer()
    except RuntimeError:
        return None

    def report(activity: dict[str, Any]) -> None:
        """发送一次带修复节点归属的工具活动。"""

        writer(
            {
                "type": "conversation.tool_activity",
                "node_name": "direct_modification_repair",
                "activity": activity,
            }
        )

    return report
