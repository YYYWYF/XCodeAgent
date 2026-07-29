from __future__ import annotations

from typing import Any, Callable

from langgraph.config import get_stream_writer

from app.agents.data_source.generator import generate_data_sources_with_deep_agent
from app.agents.frontend.generator import generate_frontend_with_deep_agent
from app.agents.repair_planner import (
    plan_build_failure_repair_with_repair_planner_agent,
)
from app.graph.nodes.common import capture_agent_file_changes, workspace_from_state
from app.graph.state import ProjectState
from app.services.build_repair_planner import (
    approve_repair_scope_confirmation,
    append_repair_tasks_to_build_plan,
    close_repaired_parent_tasks,
    create_build_failure_repair_plan,
)
from app.graph.nodes.confirmation import extract_confirmation_answer, user_confirmed_text
from app.services.build_result_coordinator import apply_agent_results_with_scheduler
from app.services.build_task_planner import (
    replace_build_task_plan_tasks,
    tasks_from_build_task_plan,
)
from app.services.build_tool_activity import task_ids_for_tool_activity
from app.services.build_scheduler import (
    mark_tasks_running,
    normalize_task_results,
    resolve_execution_slice,
    select_ready_build_batch,
    summarize_build_runtime,
    verify_task_file_changes,
)
from app.workspace.code_changes import code_change_state_update, merge_code_change_sets
from app.workspace.plan_documents import (
    project_plan_json_path,
    write_project_plan_document,
)
from app.workspace.task_documents import (
    write_build_task_dag_markdown,
    write_build_task_plan_json,
)
from app.workspace.task_documents import write_repair_task_plan_json


Runner = Callable[..., list[dict[str, Any]]]
ProgressWriter = Callable[[dict[str, Any]], None]
BatchToolActivityCallback = Callable[
    [list[dict[str, Any]], dict[str, Any] | None],
    None,
]


def _runner_for_owner(owner: str) -> tuple[str, Runner] | None:
    """根据 v3 任务 owner 选择当前可用的代码执行器。"""

    if owner == "backend":
        return "backend.deep_agent", generate_data_sources_with_deep_agent
    if owner == "frontend":
        return "frontend.deep_agent", generate_frontend_with_deep_agent
    return None


def _group_tasks_by_owner(tasks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按任务 owner 分组，便于分别派发给对应专业 Agent。"""

    groups: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        groups.setdefault(str(task.get("owner") or ""), []).append(task)
    return groups


def _runner_exception_results(
    *,
    owner: str,
    owner_tasks: list[dict[str, Any]],
    exc: Exception,
) -> list[dict[str, Any]]:
    """把专业 Agent 执行异常转换为每个任务可展示的失败结果。"""

    reason = f"{type(exc).__name__}: {exc}"
    display_reason = f"任务执行器异常退出：{reason}"
    return [
        {
            "task_id": task["id"],
            "owner": owner or task.get("owner"),
            "status": "failed",
            "failure_category": "runner_crash",
            "failure_reason": display_reason,
            "agent_note": display_reason,
            "changed_files": [],
            "commands": [],
            "change_request": None,
        }
        for task in owner_tasks
    ]


def _execute_ready_tasks(
    state: ProjectState,
    ready_tasks: list[dict[str, Any]],
    *,
    on_batch_tool_activity: BatchToolActivityCallback | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把同一批就绪任务和所选技能集合分发给对应 Deep Agent。"""

    workspace = workspace_from_state(state)
    all_results: list[dict[str, Any]] = []
    code_change_sets: list[dict[str, Any]] = []
    for owner, owner_tasks in _group_tasks_by_owner(ready_tasks).items():
        runner_entry = _runner_for_owner(owner)
        if runner_entry is None:
            all_results.extend(
                normalize_task_results(
                    dispatched_tasks=owner_tasks,
                    raw_results=[
                        {
                            "task_id": task["id"],
                            "owner": owner,
                            "status": "failed",
                            "failure_category": "runner_protocol_error",
                            "agent_note": f"No CodeRunner is registered for owner: {owner}.",
                        }
                        for task in owner_tasks
                    ],
                )
            )
            continue

        source_tool, runner = runner_entry
        try:
            captured = capture_agent_file_changes(
                workspace=workspace,
                source_tool=source_tool,
                action=lambda owner_tasks=owner_tasks, runner=runner, owner=owner: runner(
                    project_plan=state["project_plan"],
                    build_task_plan=state["build_task_plan"],
                    tasks=owner_tasks,
                    workspace=workspace,
                    selected_skill_names=state.get("selected_skill_names"),
                    **({"page_template": state.get("page_template")} if owner == "frontend" else {}),
                    on_tool_activity=(
                        (
                            lambda activity, owner_tasks=owner_tasks: on_batch_tool_activity(
                                owner_tasks,
                                activity,
                            )
                        )
                        if on_batch_tool_activity is not None
                        else None
                    ),
                ),
            )
        except Exception as exc:
            all_results.extend(
                normalize_task_results(
                    dispatched_tasks=owner_tasks,
                    raw_results=_runner_exception_results(
                        owner=owner,
                        owner_tasks=owner_tasks,
                        exc=exc,
                    ),
                )
            )
            continue
        finally:
            if on_batch_tool_activity is not None:
                on_batch_tool_activity(owner_tasks, None)
        if captured.code_change_set:
            code_change_sets.append(captured.code_change_set)
        normalized_results = normalize_task_results(
            dispatched_tasks=owner_tasks,
            raw_results=captured.value,
        )
        verified_results = verify_task_file_changes(
            results=normalized_results,
            code_change_set=captured.code_change_set,
            tasks=owner_tasks,
            workspace_root=workspace,
        )
        all_results.extend(verified_results)
    return all_results, code_change_sets


def _plan_build_repair_with_repair_planner(
    state: ProjectState,
    repair_input: dict[str, Any],
) -> dict[str, Any]:
    """让修复规划 Agent 继承当前工作流的技能集合。"""

    return plan_build_failure_repair_with_repair_planner_agent(
        repair_input=repair_input,
        workspace=workspace_from_state(state),
        selected_skill_names=state.get("selected_skill_names"),
    )


def _apply_scheduler_results(
    state: ProjectState,
    *,
    tasks: list[dict[str, Any]],
    results: list[dict[str, Any]],
    stage: str,
) -> dict[str, Any]:
    updated = apply_agent_results_with_scheduler(
        project_plan=state["project_plan"],
        build_task_plan=replace_build_task_plan_tasks(state["build_task_plan"], tasks),
        tasks=tasks,
        existing_results=state.get("build_results", []),
        new_results=results,
        stage=stage,
    )
    repaired_tasks = close_repaired_parent_tasks(
        tasks=updated["tasks"],
        results=updated.get("build_results", []),
    )
    if repaired_tasks != updated["tasks"]:
        updated["tasks"] = repaired_tasks
        updated["build_task_plan"] = replace_build_task_plan_tasks(
            updated["build_task_plan"],
            repaired_tasks,
        )
        updated["build_summary"] = summarize_build_runtime(
            repaired_tasks,
            updated.get("build_results", []),
        )
    project_plan_path = write_project_plan_document(state, updated["project_plan"])
    build_task_plan_path = write_build_task_plan_json(state, updated["build_task_plan"])
    build_task_dag_path = write_build_task_dag_markdown(state, updated["build_task_plan"])
    return {
        **updated,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": str(project_plan_json_path(state)),
        "build_task_plan_path": build_task_plan_path,
        "build_task_dag_path": build_task_dag_path,
    }


def _results_for_tasks(
    results: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """只保留当前执行切片内任务对应的构建结果。"""

    task_ids = {str(task.get("id") or "") for task in tasks}
    return [result for result in results if str(result.get("task_id") or "") in task_ids]


def _build_progress_writer() -> ProgressWriter | None:
    """在 LangGraph 节点上下文中获取实时进度写入器，直接单测时返回空。"""

    try:
        return get_stream_writer()
    except (KeyError, RuntimeError):
        return None


def _emit_build_progress(
    progress_writer: ProgressWriter | None,
    *,
    current_state: ProjectState,
    build_execution_scope: dict[str, Any] | None,
    build_events: list[str],
    message: str,
    active_tool_activities: dict[str, dict[str, Any]] | None = None,
    ephemeral: bool = False,
) -> None:
    """向 AG-UI 自定义流发送当前构建切片，供前端实时刷新任务进度。"""

    if progress_writer is None:
        return
    execution_slice = resolve_execution_slice(
        build_task_plan=current_state["build_task_plan"],
        tasks=current_state["tasks"],
        build_execution_scope=build_execution_scope,
    )
    build_summary = summarize_build_runtime(
        execution_slice["tasks"],
        _results_for_tasks(
            current_state.get("build_results", []),
            execution_slice["tasks"],
        ),
    )
    activities = active_tool_activities or {}
    execution_slice["tasks"] = [
        {
            **task,
            **(
                {"activeToolActivity": activities[str(task.get("id") or "")]}
                if task.get("status") == "running"
                and str(task.get("id") or "") in activities
                else {}
            ),
        }
        for task in execution_slice["tasks"]
    ]
    progress_writer(
        {
            "type": "workflow.build.progress",
            "node_name": "build",
            "phase": "build",
            "status": "running",
            "message": message,
            "ephemeral": ephemeral,
            "state": {
                "phase": "build",
                "status": "running",
                "tasks": current_state["tasks"],
                "build_results": current_state.get("build_results", []),
                "build_summary": build_summary,
                "build_execution_scope": build_execution_scope,
                "build_execution_slice": execution_slice,
                "build_events": list(build_events),
                "timeline": ["build"],
            },
        }
    )


def _repair_scope_confirmation_payload(repair_task_plan: dict[str, Any]) -> dict[str, Any]:
    """把修复范围扩大请求映射为稳定的 AG-UI 人工确认载荷。"""

    plan_id = str(repair_task_plan.get("planId") or "")
    requested_paths = [
        str(path) for path in repair_task_plan.get("requestedPaths", []) if str(path).strip()
    ]
    requested_resources = [
        dict(item)
        for item in repair_task_plan.get("requestedResources", [])
        if isinstance(item, dict)
    ]
    reasons = [
        str(item.get("reason") or "")
        for item in repair_task_plan.get("requires_user_confirmation", [])
        if isinstance(item, dict) and item.get("reason")
    ]
    path_text = "、".join(requested_paths) or "未提供额外路径"
    reason_text = "；".join(reasons) or "修复需要用户批准范围。"
    return {
        "mode": "repair_scope_confirmation",
        "status": "requires_user_input",
        "message": "修复计划请求扩大或确认代码修改范围。",
        "planId": plan_id,
        "requestedPaths": requested_paths,
        "requestedResources": requested_resources,
        "reason": reason_text,
        "questions": [
            {
                "id": "repair_scope_confirmation",
                "header": "修复范围",
                "question": f"计划 {plan_id} 请求修改：{path_text}。原因：{reason_text}。是否批准？",
                "type": "text",
                "placeholder": "回复“批准修复范围”或“拒绝修复范围”。",
            }
        ],
    }
def run_build_scheduler(
    state: ProjectState,
    *,
    progress_writer: ProgressWriter | None = None,
) -> dict[str, Any]:
    """按 build_execution_scope 裁剪任务图，并持续调度到当前切片完成或阻塞。"""

    build_task_plan = dict(state.get("build_task_plan") or {})
    canonical_tasks = list(
        state.get("tasks") or tasks_from_build_task_plan(build_task_plan)
    )
    build_task_plan = replace_build_task_plan_tasks(build_task_plan, canonical_tasks)
    incoming_repair_task_plan = state.get("repair_task_plan")
    request = str(state.get("request") or "")
    scope_confirmation_pending = (
        isinstance(incoming_repair_task_plan, dict)
        and incoming_repair_task_plan.get("decision") == "requires_user_confirmation"
    )
    scope_confirmation_rejected = scope_confirmation_pending and any(
        signal in extract_confirmation_answer(request).replace(" ", "")
        for signal in ("拒绝", "不同意", "不批准")
    )
    if (
        scope_confirmation_pending
        and user_confirmed_text(
            request,
            positive_signals=("批准", "同意", "确认"),
            negative_signals=("拒绝", "不同意", "不批准"),
        )
    ):
        incoming_repair_task_plan = approve_repair_scope_confirmation(
            incoming_repair_task_plan
        )
        scope_confirmation_pending = False
    if scope_confirmation_pending:
        tasks = tasks_from_build_task_plan(build_task_plan)
        execution_slice = resolve_execution_slice(
            build_task_plan=build_task_plan,
            tasks=tasks,
            build_execution_scope=state.get("build_execution_scope"),
        )
        build_summary = {
            **summarize_build_runtime(
                execution_slice["tasks"],
                _results_for_tasks(
                    state.get("build_results", []), execution_slice["tasks"]
                ),
            ),
            "status": "failed" if scope_confirmation_rejected else "requires_confirmation",
        }
        return {
            "phase": "build",
            "status": "failed" if scope_confirmation_rejected else "requires_user_input",
            "tasks": tasks,
            "build_task_plan": build_task_plan,
            "build_results": list(state.get("build_results", [])),
            "build_summary": build_summary,
            "build_execution_scope": state.get("build_execution_scope"),
            "build_execution_slice": execution_slice,
            "repair_task_plan": (
                {**incoming_repair_task_plan, "decision": "terminal_failure", "status": "terminal_failure"}
                if scope_confirmation_rejected
                else incoming_repair_task_plan
            ),
            "repair_tasks": [],
            "clarification": (
                {}
                if scope_confirmation_rejected
                else _repair_scope_confirmation_payload(incoming_repair_task_plan)
            ),
            "build_events": [
                "scheduler:repair_scope_rejected"
                if scope_confirmation_rejected
                else "scheduler:repair_requires_confirmation"
            ],
            "timeline": ["build"],
        }
    if (
        isinstance(incoming_repair_task_plan, dict)
        and incoming_repair_task_plan.get("tasks")
        and incoming_repair_task_plan.get("decision", "repair") == "repair"
    ):
        build_task_plan = append_repair_tasks_to_build_plan(
            build_task_plan=build_task_plan,
            repair_task_plan=incoming_repair_task_plan,
        )

    tasks = tasks_from_build_task_plan(build_task_plan)
    if not tasks:
        return {
            "phase": "build",
            "ready_tasks": [],
            "build_summary": {
                "total": 0,
                "completed": 0,
                "failed": 0,
                "pending": 0,
                "results": len(state.get("build_results", [])),
                "status": "completed",
            },
            "build_events": ["scheduler:no_tasks"],
            "status": "completed",
        }

    current_state: ProjectState = {
        **state,
        "tasks": tasks,
        "build_task_plan": replace_build_task_plan_tasks(build_task_plan, tasks),
        "build_results": list(state.get("build_results", [])),
    }
    build_execution_scope = state.get("build_execution_scope")
    execution_slice = resolve_execution_slice(
        build_task_plan=current_state["build_task_plan"],
        tasks=current_state["tasks"],
        build_execution_scope=build_execution_scope,
    )
    build_events: list[str] = []
    all_code_change_sets: list[dict[str, Any]] = []
    repair_task_plan: dict[str, Any] = (
        incoming_repair_task_plan
        if isinstance(incoming_repair_task_plan, dict)
        else state.get("repair_task_plan", {})
    )
    repair_task_plan_path = state.get("repair_task_plan_path")
    repair_dispatched = False
    max_iterations = max(len(tasks) * 2, 1)
    progress_writer = progress_writer if progress_writer is not None else _build_progress_writer()

    for iteration in range(1, max_iterations + 1):
        execution_slice = resolve_execution_slice(
            build_task_plan=current_state["build_task_plan"],
            tasks=current_state["tasks"],
            build_execution_scope=build_execution_scope,
        )
        slice_tasks = execution_slice["tasks"]
        if not slice_tasks:
            build_events.append("scheduler:no_tasks_in_scope")
            break

        selection = select_ready_build_batch(slice_tasks)
        if selection["errors"]:
            build_events.append("scheduler:invalid_dag")
            break
        if selection["is_complete"]:
            build_events.append("scheduler:completed")
            break
        ready_tasks = selection["ready_tasks"]
        repair_dispatched = repair_dispatched or any(
            task.get("kind") == "repair" for task in ready_tasks
        )
        if not ready_tasks:
            build_events.append("scheduler:blocked")
            break

        ready_ids = selection["ready_task_ids"]
        build_events.append(f"scheduler:dispatch:{','.join(ready_ids)}")
        running_tasks = mark_tasks_running(current_state["tasks"], ready_ids)
        active_tool_activities: dict[str, dict[str, Any]] = {}
        running_message = f"正在执行构建任务：{', '.join(ready_ids)}"
        _emit_build_progress(
            progress_writer,
            current_state={**current_state, "tasks": running_tasks},
            build_execution_scope=build_execution_scope,
            build_events=build_events,
            message=running_message,
        )

        def update_batch_tool_activity(
            owner_tasks: list[dict[str, Any]],
            activity: dict[str, Any] | None,
        ) -> None:
            """让批次内最新工具活动覆盖旧值，并发送不进入历史事件的临时切片。"""

            owner_task_ids = [str(task.get("id") or "") for task in owner_tasks]
            if activity is None:
                for task_id in owner_task_ids:
                    active_tool_activities.pop(task_id, None)
            else:
                for task_id in task_ids_for_tool_activity(activity, owner_tasks):
                    active_tool_activities[task_id] = activity
            _emit_build_progress(
                progress_writer,
                current_state={**current_state, "tasks": running_tasks},
                build_execution_scope=build_execution_scope,
                build_events=build_events,
                message=running_message,
                active_tool_activities=active_tool_activities,
                ephemeral=True,
            )

        results, code_change_sets = _execute_ready_tasks(
            {**current_state, "tasks": running_tasks},
            ready_tasks,
            on_batch_tool_activity=update_batch_tool_activity,
        )
        all_code_change_sets.extend(code_change_sets)
        updated = _apply_scheduler_results(
            {**current_state, "tasks": running_tasks},
            tasks=running_tasks,
            results=results,
            stage=f"build_scheduler_iteration_{iteration}",
        )
        current_state = {**current_state, **updated}
        build_events.append(f"scheduler:results:{len(results)}")
        _emit_build_progress(
            progress_writer,
            current_state=current_state,
            build_execution_scope=build_execution_scope,
            build_events=build_events,
            message=f"构建任务结果已更新：{len(results)} 个任务返回结果。",
        )

        summary = summarize_build_runtime(
            resolve_execution_slice(
                build_task_plan=current_state["build_task_plan"],
                tasks=current_state["tasks"],
                build_execution_scope=build_execution_scope,
            )["tasks"],
            _results_for_tasks(current_state.get("build_results", []), slice_tasks),
        )
        if summary["status"] == "needs_repair":
            repair_task_plan = create_build_failure_repair_plan(
                failed_results=[
                    result
                    for result in _results_for_tasks(
                        current_state.get("build_results", []),
                        slice_tasks,
                    )
                    if result.get("status") == "failed"
                ],
                tasks=current_state["tasks"],
                existing_repair_tasks=[
                    task for task in current_state["tasks"] if task.get("kind") == "repair"
                ],
                workspace_snapshot=current_state.get("workspace_snapshot"),
                repair_planner=lambda repair_input: _plan_build_repair_with_repair_planner(
                    current_state,
                    repair_input,
                ),
            )
            repair_task_plan_path = write_repair_task_plan_json(
                current_state,
                repair_task_plan,
            )
            if repair_task_plan.get("decision") == "requires_user_confirmation":
                current_state = {
                    **current_state,
                    "repair_task_plan": repair_task_plan,
                    "repair_task_plan_path": repair_task_plan_path,
                }
                build_events.append("scheduler:repair_requires_confirmation")
                break
            if repair_task_plan.get("decision") == "terminal_failure":
                current_state = {
                    **current_state,
                    "repair_task_plan": repair_task_plan,
                    "repair_task_plan_path": repair_task_plan_path,
                }
                build_events.append("scheduler:repair_terminal_failure")
                break
            if not repair_task_plan.get("tasks"):
                build_events.append("scheduler:repair_unavailable")
                break
            next_build_task_plan = append_repair_tasks_to_build_plan(
                build_task_plan=current_state["build_task_plan"],
                repair_task_plan=repair_task_plan,
            )
            current_state = {
                **current_state,
                "build_task_plan": next_build_task_plan,
                "tasks": tasks_from_build_task_plan(next_build_task_plan),
                "repair_task_plan": repair_task_plan,
                "repair_task_plan_path": repair_task_plan_path,
                "repair_tasks": repair_task_plan["tasks"],
            }
            build_task_plan_path = write_build_task_plan_json(
                current_state,
                next_build_task_plan,
            )
            build_task_dag_path = write_build_task_dag_markdown(
                current_state,
                next_build_task_plan,
            )
            current_state["build_task_plan_path"] = build_task_plan_path
            current_state["build_task_dag_path"] = build_task_dag_path
            build_events.append(f"scheduler:repair_planned:{len(repair_task_plan['tasks'])}")
            _emit_build_progress(
                progress_writer,
                current_state=current_state,
                build_execution_scope=build_execution_scope,
                build_events=build_events,
                message=f"已生成修复任务：{len(repair_task_plan['tasks'])} 个。",
            )
            continue

        if summary["status"] in {"requires_confirmation", "failed"}:
            build_events.append(f"scheduler:{summary['status']}")
            break
    else:
        build_events.append("scheduler:iteration_budget_exhausted")

    build_results = current_state.get("build_results", [])
    execution_slice = resolve_execution_slice(
        build_task_plan=current_state["build_task_plan"],
        tasks=current_state["tasks"],
        build_execution_scope=build_execution_scope,
    )
    build_summary = summarize_build_runtime(
        execution_slice["tasks"],
        _results_for_tasks(build_results, execution_slice["tasks"]),
    )
    merged_code_changes = merge_code_change_sets(all_code_change_sets)
    workflow_status = (
        "completed"
        if build_summary.get("status") == "completed"
        else "requires_user_input"
        if build_summary.get("status") == "requires_confirmation"
        else "failed"
    )
    clarification = (
        _repair_scope_confirmation_payload(repair_task_plan)
        if isinstance(repair_task_plan, dict)
        and repair_task_plan.get("decision") == "requires_user_confirmation"
        else {}
    )

    return {
        "phase": "build",
        "project_plan": current_state.get("project_plan", state.get("project_plan", {})),
        "project_plan_path": current_state.get(
            "project_plan_path", state.get("project_plan_path")
        ),
        "project_plan_json_path": current_state.get(
            "project_plan_json_path", state.get("project_plan_json_path")
        ),
        "build_task_plan": current_state.get(
            "build_task_plan", state.get("build_task_plan", {})
        ),
        "build_task_plan_path": current_state.get(
            "build_task_plan_path", state.get("build_task_plan_path")
        ),
        "build_task_dag_path": current_state.get(
            "build_task_dag_path", state.get("build_task_dag_path")
        ),
        "tasks": current_state["tasks"],
        "ready_tasks": [],
        "build_results": build_results,
        "build_summary": build_summary,
        "status": workflow_status,
        "clarification": clarification,
        "build_execution_scope": build_execution_scope,
        "build_execution_slice": execution_slice,
        "repair_task_plan": repair_task_plan,
        "repair_task_plan_path": repair_task_plan_path,
        "repair_tasks": repair_task_plan.get("tasks", []) if isinstance(repair_task_plan, dict) else [],
        "build_events": build_events,
        "repair_iteration": int(state.get("repair_iteration", 0) or 0)
        + (1 if repair_dispatched else 0),
        **code_change_state_update(merged_code_changes),
        "timeline": ["build"],
    }


def build(state: ProjectState) -> dict:
    """运行构建调度节点，并在 LangGraph 流中报告逐任务进度。"""

    return run_build_scheduler(state)
