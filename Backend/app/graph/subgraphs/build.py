from __future__ import annotations

import contextvars
from uuid import uuid4

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from langgraph.config import get_stream_writer

from app.config import dag_business_self_check_enabled
from app.agents.database.generator import generate_database_with_deep_agent
from app.agents.data_source.generator import generate_data_sources_with_deep_agent
from app.agents.frontend.generator import generate_frontend_with_deep_agent
from app.agents.repair_planner import (
    plan_build_failure_repair_with_repair_planner_agent,
)
from app.graph.nodes.common import (
    capture_agent_file_changes,
    refresh_code_graph_after_changes,
    workspace_from_state,
)
from app.graph.state import ProjectState
from app.services.build_repair_planner import (
    approve_repair_scope_confirmation,
    append_repair_tasks_to_build_plan,
    close_repaired_parent_tasks,
    create_build_failure_repair_plan,
)
from app.graph.nodes.confirmation import extract_confirmation_answer, user_confirmed_text
from app.services.build_result_coordinator import apply_agent_results_with_scheduler
from app.services.authorization_platform_projection import (
    AuthorizationPlatformProjectionError,
    apply_authorization_platform_projections,
)
from app.services.authorization_edd import verify_authorization_edd
from app.services.business_acceptance_verifier import verify_business_acceptance
from app.services.build_task_planner import (
    replace_build_task_plan_tasks,
    tasks_from_build_task_plan,
)
from app.services.build_tool_activity import (
    path_matches_task_scope,
    task_ids_for_tool_activity,
)
from app.services.build_scheduler import (
    attribute_task_file_changes,
    classify_task_result,
    mark_tasks_running,
    normalize_task_results,
    ready_repair_task_ids,
    reset_failed_tasks_for_retry,
    resolve_execution_slice,
    retryable_failed_task_ids,
    select_ready_build_batch,
    summarize_build_runtime,
    hydrate_missing_failed_results,
)
from app.workspace.code_changes import (
    build_code_change_set,
    code_change_state_update,
    merge_code_change_sets,
)
from app.workspace.task_documents import (
    build_run_task_plan_json_path,
    build_task_plan_sha256,
    build_task_plan_json_path,
    load_build_task_plan_json,
    write_build_run_task_plan_json,
)
from app.workspace.task_documents import write_repair_task_plan_json
from app.workspace.workspace_snapshot_documents import load_workspace_snapshot_json


Runner = Callable[..., list[dict[str, Any]]]
ProgressWriter = Callable[[dict[str, Any]], None]
BatchToolActivityCallback = Callable[
    [list[dict[str, Any]], dict[str, Any] | None],
    None,
]
MAX_PARALLEL_BUILD_TASKS = 3


def _runner_for_owner(owner: str) -> tuple[str, Runner] | None:
    """根据 v3 任务 owner 选择当前可用的代码执行器。"""

    if owner == "database":
        return "database.deep_agent", generate_database_with_deep_agent
    if owner == "backend":
        return "backend.deep_agent", generate_data_sources_with_deep_agent
    if owner == "frontend":
        return "frontend.deep_agent", generate_frontend_with_deep_agent
    return None


def _approved_database_change_plan(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """从审批暂停恢复的任务上读取已批准并待执行的数据库计划。"""

    for task in tasks:
        plan = task.get("approved_database_change_plan")
        if isinstance(plan, dict) and plan:
            return plan
    return None


def _workspace_snapshot_from_state(state: ProjectState) -> dict[str, Any]:
    """为 backend owner 读取检查阶段生成的完整 WorkspaceSnapshot。"""

    snapshot = state.get("workspace_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        return snapshot
    snapshot_path = state.get("workspace_snapshot_path")
    if snapshot_path:
        return load_workspace_snapshot_json(snapshot_path)
    return {}


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


def _database_approval_result(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """从数据库 Agent 结果中找出需要用户审批的高危操作。"""

    return next(
        (
            result
            for result in results
            if result.get("failure_category") == "database_approval_required"
            and isinstance(result.get("database_approval"), dict)
        ),
        None,
    )


def _reset_running_tasks_to_pending(
    tasks: list[dict[str, Any]],
    ready_task_ids: list[str],
) -> list[dict[str, Any]]:
    """数据库审批暂停时撤销本轮 running 标记，方便用户批准后继续调度。"""

    ready = set(ready_task_ids)
    return [
        (
            {
                **task,
                "status": "pending",
                "scheduler": {
                    **(
                        task.get("scheduler")
                        if isinstance(task.get("scheduler"), dict)
                        else {}
                    ),
                    "paused_for": "database_approval",
                },
            }
            if task.get("id") in ready and task.get("status") == "running"
            else task
        )
        for task in tasks
    ]


def _database_approval_payload(result: dict[str, Any]) -> dict[str, Any]:
    """把数据库高危审批结果转换为 Workflow clarification。"""

    approval = result.get("database_approval") if isinstance(result.get("database_approval"), dict) else {}
    risk = result.get("database_risk") if isinstance(result.get("database_risk"), dict) else {}
    plan = result.get("database_change_plan") if isinstance(result.get("database_change_plan"), dict) else {}
    statements = plan.get("statements") if isinstance(plan.get("statements"), list) else []
    return {
        "mode": "agent_approval",
        "status": "requires_user_input",
        "message": "数据库高危操作需要审批后才能执行。",
        "approval": approval,
        "tool": approval.get("tool") or "database.execute",
        "risk": risk,
        "database_change_plan": plan,
        "questions": [
            {
                "id": "database_approval",
                "header": "数据库审批",
                "question": str(
                    approval.get("description")
                    or "是否批准执行该高危数据库变更计划？"
                ),
                "type": "text",
                "placeholder": "在审批卡片中批准或拒绝；批准后继续当前工作流。",
            }
        ],
        "context": {
            "taskId": result.get("task_id"),
            "subject": approval.get("subject"),
            "details": approval.get("details"),
            "statementCount": len(statements),
        },
    }


def _database_approval_rejected_result(
    *,
    state: ProjectState,
    tasks: list[dict[str, Any]],
    paused_tasks: list[dict[str, Any]],
    build_task_plan: dict[str, Any],
) -> dict[str, Any]:
    """用户拒绝高危数据库审批后，把待审批数据库任务标记失败并结束本轮构建。"""

    paused_ids = {str(task.get("id") or "") for task in paused_tasks}
    rejected_results = [
        {
            "task_id": task["id"],
            "owner": str(task.get("owner") or "database"),
            "status": "failed",
            "failure_category": "database_approval_rejected",
            "failure_reason": "用户拒绝了高危数据库审批，数据库变更未执行。",
            "agent_note": "用户拒绝了高危数据库审批，数据库变更未执行。",
            "changed_files": [],
            "commands": [],
            "change_request": None,
        }
        for task in paused_tasks
    ]
    next_tasks = [
        (
            {
                **task,
                "status": "failed",
                "scheduler": {
                    **(
                        task.get("scheduler")
                        if isinstance(task.get("scheduler"), dict)
                        else {}
                    ),
                    "paused_for": "database_approval_rejected",
                },
            }
            if str(task.get("id") or "") in paused_ids
            else task
        )
        for task in tasks
    ]
    build_results = [
        *(
            state.get("build_results")
            if isinstance(state.get("build_results"), list)
            else []
        ),
        *rejected_results,
    ]
    execution_slice = resolve_execution_slice(
        build_task_plan=build_task_plan,
        tasks=next_tasks,
        build_execution_scope=state.get("build_execution_scope"),
    )
    build_summary = {
        **summarize_build_runtime(
            execution_slice["tasks"],
            _results_for_tasks(build_results, execution_slice["tasks"]),
        ),
        "status": "failed",
    }
    return {
        "phase": "build",
        "status": "failed",
        "tasks": next_tasks,
        "build_task_plan": replace_build_task_plan_tasks(
            build_task_plan,
            next_tasks,
        ),
        "build_results": build_results,
        "build_summary": build_summary,
        "build_execution_scope": state.get("build_execution_scope"),
        "build_execution_slice": execution_slice,
        "clarification": {},
        "database_approval_requests": [],
        "database_change_plan": {},
        "build_events": ["scheduler:database_approval_rejected"],
        "timeline": ["build"],
    }


def _execute_ready_tasks(
    state: ProjectState,
    ready_tasks: list[dict[str, Any]],
    *,
    on_batch_tool_activity: BatchToolActivityCallback | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把同一批就绪任务逐任务并发分发，隔离 Agent 写入归属和验收状态。"""

    all_results: list[dict[str, Any]] = []
    code_change_sets: list[dict[str, Any]] = []
    with ThreadPoolExecutor(
        max_workers=max(1, min(len(ready_tasks), MAX_PARALLEL_BUILD_TASKS)),
        thread_name_prefix="build-task",
    ) as executor:
        # 把节点线程的 LangGraph 运行上下文复制进每个任务工作线程；
        # 否则 custom 模式 stream writer 在回调中调用 get_config() 时缺少上下文，会抛 RuntimeError。
        futures = [
            executor.submit(
                contextvars.copy_context().run,
                _execute_owner_tasks,
                state,
                str(task.get("owner") or ""),
                [task],
                on_batch_tool_activity=on_batch_tool_activity,
            )
            for task in ready_tasks
        ]
        # 按提交顺序归并结果，保持任务结果和事件展示稳定。
        for future in futures:
            owner_results, owner_change_set = future.result()
            all_results.extend(owner_results)
            if owner_change_set:
                code_change_sets.append(owner_change_set)
    # Build Agent 可能执行编译或生成代码，target、generated-sources 等工程产物
    # 会自然出现在批次快照中；这些差异不再作为任务失败条件。
    return all_results, code_change_sets


def _execute_owner_tasks(
    state: ProjectState,
    owner: str,
    owner_tasks: list[dict[str, Any]],
    *,
    on_batch_tool_activity: BatchToolActivityCallback | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """执行一个任务 Agent，并只使用该 Agent 的真实写入完成文件归属。"""

    runner_entry = _runner_for_owner(owner)
    if runner_entry is None:
        return (
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
            ),
            None,
        )

    workspace = workspace_from_state(state)
    source_tool, runner = runner_entry
    mutation_paths: set[str] = set()

    def record_tool_activity(activity: dict[str, Any]) -> None:
        """记录当前任务 Agent 的文件写入意图，并继续投射实时工具活动。"""

        if str(activity.get("category") or "") in {"write", "delete"}:
            normalized_path = _normalize_tool_activity_path(activity.get("path"))
            if normalized_path:
                mutation_paths.add(normalized_path)
        if on_batch_tool_activity is not None:
            on_batch_tool_activity(owner_tasks, activity)

    try:
        captured = capture_agent_file_changes(
            workspace=workspace,
            source_tool=source_tool,
            action=lambda: runner(
                project_plan=state["project_plan"],
                build_task_plan=state["build_task_plan"],
                tasks=owner_tasks,
                workspace=workspace,
                selected_skill_names=state.get("selected_skill_names"),
                **(
                    {"workspace_snapshot": _workspace_snapshot_from_state(state)}
                    if owner == "backend"
                    else {}
                ),
                **({"page_template": state.get("page_template")} if owner == "frontend" else {}),
                **({"ui_designs": state.get("ui_designs")} if owner == "frontend" else {}),
                **(
                    {"database_change_plan": _approved_database_change_plan(owner_tasks)}
                    if owner == "database"
                    else {}
                ),
                # 即使前端没有订阅工具进度也必须记录写入，文件归属不能依赖 UI 回调。
                on_tool_activity=record_tool_activity,
            ),
        )
    except Exception as exc:
        return (
            normalize_task_results(
                dispatched_tasks=owner_tasks,
                raw_results=_runner_exception_results(
                    owner=owner,
                    owner_tasks=owner_tasks,
                    exc=exc,
                ),
            ),
            None,
        )
    finally:
        if on_batch_tool_activity is not None:
            on_batch_tool_activity(owner_tasks, None)

    owner_change_set = _filter_change_set_for_tasks(
        captured.code_change_set,
        owner_tasks,
        source_tool=source_tool,
        mutation_paths=mutation_paths,
    )
    normalized_results = normalize_task_results(
        dispatched_tasks=owner_tasks,
        raw_results=captured.value,
    )
    attributed_results = (
        normalized_results
        if owner == "database"
        else attribute_task_file_changes(
            results=normalized_results,
            code_change_set=owner_change_set,
            tasks=owner_tasks,
        )
    )
    # Agent 返回的验收字段不是可信证据，先清除后只使用本轮确定性验证结果。
    sanitized_results = [
        {
            key: value
            for key, value in result.items()
            if key
            not in {
                "acceptance_evidence",
                "business_acceptance_evidence",
                "business_acceptance_summary",
            }
        }
        for result in attributed_results
    ]
    sanitized_results = _verify_business_results(
        state,
        owner_tasks,
        sanitized_results,
        workspace_root=str(workspace) if workspace else None,
    )
    return sanitized_results, owner_change_set


def _verify_business_results(
    state: ProjectState,
    owner_tasks: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    workspace_root: str | None,
) -> list[dict[str, Any]]:
    """独立执行业务验收，并在全部检查完成后统一汇总任务状态。"""

    tasks_by_id = {
        str(task.get("id") or ""): task
        for task in owner_tasks
        if task.get("id")
    }
    if not dag_business_self_check_enabled():
        return [
            _skip_business_acceptance(
                result,
                tasks_by_id.get(str(result.get("task_id") or ""), {}),
            )
            for result in results
        ]

    dependency_evidence = _completed_dependency_business_evidence(
        state.get("build_results"),
        owner_tasks,
    )
    verified: list[dict[str, Any]] = []
    for result in results:
        task = tasks_by_id.get(str(result.get("task_id") or ""), {})
        if result.get("status") not in {"completed", "already_satisfied"}:
            verified.append(result)
            continue
        business = verify_business_acceptance(
            task,
            workspace_root,
            formal_artifacts=state.get("project_plan")
            if isinstance(state.get("project_plan"), dict)
            else None,
            dependency_evidence=dependency_evidence.get(str(task.get("id") or ""), []),
        )
        next_result = {
            **result,
            "business_acceptance_evidence": business["business_acceptance_evidence"],
            "business_acceptance_summary": business["business_acceptance_summary"],
            "acceptance_status": {
                **(
                    result.get("acceptance_status")
                    if isinstance(result.get("acceptance_status"), dict)
                    else {}
                ),
                "business": business.get("status"),
            },
        }
        if business.get("status") == "failed":
            _merge_business_acceptance_failure(next_result, "business_acceptance_failed")
        elif business.get("status") == "blocked":
            _merge_business_acceptance_failure(next_result, "business_acceptance_blocked")
        elif next_result.get("status") in {"completed", "already_satisfied"}:
            next_result["failure_category"] = None
            next_result["failure_reason"] = None
            next_result["scheduler_decision"] = classify_task_result(next_result)
        verified.append(next_result)
    return verified


def _skip_business_acceptance(
    result: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any]:
    """关闭业务自检时记录明确的跳过证据，并保留任务执行结果。"""

    checks = [
        check
        for check in task.get("business_acceptance_checks") or []
        if isinstance(check, dict)
    ]
    evidence = [
        {
            "check_id": str(check.get("id") or ""),
            "kind": str(check.get("kind") or ""),
            "status": "skipped",
            "evidence": "DAG 业务自检已通过环境变量关闭，本次自动跳过。",
        }
        for check in checks
    ]
    next_result = {
        **result,
        "business_acceptance_evidence": evidence,
        "business_acceptance_summary": {
            "total": len(evidence),
            "passed": 0,
            "failed": 0,
            "blocked": 0,
            "skipped": len(evidence),
            "duration_ms_total": 0,
            "duration_ms_avg": 0,
            "by_kind": {},
        },
        "acceptance_status": {
            **(
                result.get("acceptance_status")
                if isinstance(result.get("acceptance_status"), dict)
                else {}
            ),
            "business": "skipped",
        },
    }
    if next_result.get("status") in {"completed", "already_satisfied"}:
        next_result["failure_category"] = None
        next_result["failure_reason"] = None
        next_result["scheduler_decision"] = classify_task_result(next_result)
    return next_result


def _completed_dependency_business_evidence(
    build_results: Any,
    tasks: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """按任务依赖隔离已完成业务验收证据，避免并发任务互相消费结果。"""

    latest_by_task: dict[str, dict[str, Any]] = {}
    for result in build_results if isinstance(build_results, list) else []:
        if not isinstance(result, dict) or not result.get("task_id"):
            continue
        latest_by_task[str(result["task_id"])] = result
    evidence_by_task: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        task_id = str(task.get("id") or "")
        dependency_evidence: list[dict[str, Any]] = []
        for dependency_id in task.get("dependencies") or []:
            result = latest_by_task.get(str(dependency_id))
            if not result or result.get("status") not in {"completed", "already_satisfied"}:
                continue
            dependency_evidence.extend(
                item
                for item in result.get("business_acceptance_evidence") or []
                if isinstance(item, dict)
            )
        evidence_by_task[task_id] = dependency_evidence
    return evidence_by_task


def _mark_business_acceptance_failure(result: dict[str, Any], category: str) -> None:
    """把业务检查失败或证据不足转换为可进入 Repair 的调度失败。"""

    summary = result.get("business_acceptance_summary")
    summary_text = (
        f"通过 {summary.get('passed', 0)}，失败 {summary.get('failed', 0)}，"
        f"阻断 {summary.get('blocked', 0)}"
        if isinstance(summary, dict)
        else "结果摘要不可用"
    )
    result["status"] = "failed"
    result["failure_category"] = category
    result["failure_reason"] = f"业务验收未通过（{summary_text}）。"
    original_note = str(result.get("agent_note") or "")
    message = f"BUSINESS VERIFICATION FAILED: {result['failure_reason']}"
    result["agent_note"] = f"{original_note}\n\n{message}" if original_note else message
    result["scheduler_decision"] = classify_task_result(result)


def _merge_business_acceptance_failure(result: dict[str, Any], category: str) -> None:
    """合并业务失败与既有工程失败，避免一种验收覆盖另一种验收状态。"""

    existing_failure = (
        str(result.get("failure_reason") or "").strip()
        if result.get("status") == "failed"
        else ""
    )
    _mark_business_acceptance_failure(result, category)
    if existing_failure:
        business_failure = str(result.get("failure_reason") or "").strip()
        result["failure_category"] = "acceptance_verification_failed"
        result["failure_reason"] = "；".join(
            dict.fromkeys(
                value for value in (existing_failure, business_failure) if value
            )
        )
        result["scheduler_decision"] = classify_task_result(result)


def _filter_change_set_for_tasks(
    change_set: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    *,
    source_tool: str,
    mutation_paths: set[str] | None = None,
) -> dict[str, Any] | None:
    """保留任务授权差异和当前 Agent 实际调用写工具产生的越界差异。"""

    if not isinstance(change_set, dict):
        return None
    owned_paths = {
        normalized
        for path in mutation_paths or set()
        if (normalized := _normalize_tool_activity_path(path))
    }
    files = [
        file_item
        for file_item in change_set.get("files", [])
        if isinstance(file_item, dict)
        and file_item.get("path")
        and (
            _normalize_tool_activity_path(file_item["path"]) in owned_paths
            or any(
                path_matches_task_scope(str(file_item["path"]), task)
                for task in tasks
            )
        )
    ]
    workspace_root = str(change_set.get("workspaceRoot") or "")
    if not files or not workspace_root:
        return None
    return build_code_change_set(
        workspace_root=workspace_root,
        files=files,
        source_tool=source_tool,
    )


def _normalize_tool_activity_path(value: Any) -> str:
    """把工具活动中的虚拟绝对路径转换为工作区相对路径。"""

    normalized = str(value or "").strip().replace("\\", "/")
    return normalized.lstrip("/").lstrip("./")


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
    # 调度状态由 Graph checkpoint 保存，不能回写 Build Run 的只读计划副本或规划权威文件。
    return updated


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
        repair_task_plan=current_state.get("repair_task_plan"),
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


def _latest_build_task_plan_for_build(
    state: ProjectState,
) -> tuple[dict[str, Any], list[str]]:
    """读取工作区最新 DAG 并执行 Build 入口的最小确认门禁。"""

    workspace = workspace_from_state(state)
    path = (
        Path(workspace).expanduser() / ".xcodeagent" / "plans" / "build-task-plan.json"
        if workspace
        else build_task_plan_json_path(state)
    )
    if not path.is_file():
        return {}, ["工作区中不存在最新 build-task-plan.json，Build 已被阻止。"]
    try:
        build_task_plan = load_build_task_plan_json(path)
    except (OSError, ValueError, TypeError):
        return {}, ["最新 build-task-plan.json 无法读取或不是有效 JSON。"]
    if not isinstance(build_task_plan, dict):
        return {}, ["最新 build-task-plan.json 根结构必须是对象。"]
    errors: list[str] = []
    if build_task_plan.get("schema_version") != "build-dag.v3":
        errors.append("最新 Build DAG schema_version 不是 build-dag.v3。")
    if build_task_plan.get("status") != "ready":
        errors.append(
            f"最新 Build DAG status={build_task_plan.get('status') or 'unknown'}，不能进入 Build。"
        )
    confirmation_status = build_task_plan.get("confirmation_status")
    if confirmation_status != "confirmed":
        errors.append(
            "Build DAG 尚未确认。"
            if confirmation_status == "pending"
            else "Build DAG 缺少有效 confirmation_status。"
        )
    graph = build_task_plan.get("task_graph")
    validation = graph.get("validation") if isinstance(graph, dict) else None
    if not isinstance(validation, dict) or validation.get("is_valid") is not True:
        graph_errors = validation.get("errors") if isinstance(validation, dict) else []
        errors.extend(str(error) for error in graph_errors if str(error).strip())
        if not graph_errors:
            errors.append("Build DAG task_graph.validation 未通过。")
    current_scope = state.get("build_execution_scope")
    current_scope = current_scope if isinstance(current_scope, dict) else {}
    planned_scope = build_task_plan.get("build_execution_scope")
    planned_scope = planned_scope if isinstance(planned_scope, dict) else {}
    if planned_scope and current_scope and planned_scope != current_scope:
        errors.append(
            "Build DAG scope 与当前 Build scope 不一致："
            f"planned={planned_scope} current={current_scope}。"
        )
    return build_task_plan, _dedupe_build_gate_errors(errors)


def _bound_build_task_plan_for_build(
    state: ProjectState,
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """创建或重读当前 Build Run 的只读计划副本，并拒绝规划文件漂移。"""

    bound_run_id = str(state.get("build_run_id") or "").strip()
    bound_path = str(state.get("build_run_plan_path") or "").strip()
    bound_sha256 = str(state.get("build_run_plan_sha256") or "").strip()
    latest_plan, latest_errors = _latest_build_task_plan_for_build(state)
    if bound_run_id or bound_path or bound_sha256:
        if not (bound_run_id and bound_path and bound_sha256):
            return {}, {}, ["当前 Build Run 的任务计划绑定不完整。"]
        try:
            expected_snapshot_path = build_run_task_plan_json_path(state, bound_run_id)
        except ValueError as exc:
            return {}, {}, [str(exc)]
        if Path(bound_path).expanduser().resolve() != expected_snapshot_path.resolve():
            return {}, {}, ["Build Run 的任务计划副本路径与运行标识不一致。"]
        if latest_errors:
            return {}, {}, ["当前规划任务计划已失效，不能继续已绑定 Build Run。", *latest_errors]
        if build_task_plan_sha256(latest_plan) != bound_sha256:
            return {}, {}, ["已绑定 Build Run 的任务计划已变化；请以新的已确认计划重新启动 Build。"]
        try:
            snapshot = load_build_task_plan_json(bound_path)
        except (OSError, ValueError, TypeError):
            return {}, {}, ["Build Run 的只读任务计划副本无法读取。"]
        if not isinstance(snapshot, dict) or build_task_plan_sha256(snapshot) != bound_sha256:
            return {}, {}, ["Build Run 的只读任务计划副本摘要不匹配。"]
        return snapshot, {
            "build_run_id": bound_run_id,
            "build_run_plan_path": bound_path,
            "build_run_plan_sha256": bound_sha256,
        }, []

    if latest_errors:
        return latest_plan, {}, latest_errors
    build_run_id = f"build-{uuid4().hex}"
    try:
        snapshot_path = write_build_run_task_plan_json(
            state,
            build_run_id=build_run_id,
            build_task_plan=latest_plan,
        )
    except (OSError, ValueError, TypeError) as exc:
        return latest_plan, {}, [f"无法创建 Build Run 的只读任务计划副本：{exc}"]
    return latest_plan, {
        "build_run_id": build_run_id,
        "build_run_plan_path": snapshot_path,
        "build_run_plan_sha256": build_task_plan_sha256(latest_plan),
    }, []


def _build_run_plan_drift_errors(state: ProjectState) -> list[str]:
    """在每次叶子任务派发前确认规划权威文件仍等于当前 Build Run 绑定。"""

    bound_sha256 = str(state.get("build_run_plan_sha256") or "").strip()
    if not bound_sha256:
        return ["当前 Build Run 缺少任务计划摘要绑定。"]
    latest_plan, latest_errors = _latest_build_task_plan_for_build(state)
    if latest_errors:
        return ["当前规划任务计划已失效，不能继续已绑定 Build Run。", *latest_errors]
    if build_task_plan_sha256(latest_plan) != bound_sha256:
        return ["已绑定 Build Run 的任务计划已变化；请以新的已确认计划重新启动 Build。"]
    return []


def _build_run_plan_drift_result(
    current_state: ProjectState,
    *,
    errors: list[str],
    build_events: list[str],
    build_execution_scope: dict[str, Any] | None,
) -> dict[str, Any]:
    """保留已执行结果并终止发生计划漂移的当前 Build Run。"""

    tasks = list(current_state.get("tasks") or [])
    return {
        "phase": "build",
        "status": "failed",
        "build_task_plan": current_state.get("build_task_plan", {}),
        "build_task_plan_path": current_state.get("build_run_plan_path"),
        "tasks": tasks,
        "build_results": list(current_state.get("build_results") or []),
        "build_summary": {
            "status": "failed",
            "total": len(tasks),
            "completed": sum(1 for task in tasks if task.get("status") == "completed"),
            "failed": sum(1 for task in tasks if task.get("status") == "failed"),
            "pending": sum(1 for task in tasks if task.get("status") == "pending"),
            "gate_errors": errors,
        },
        "build_execution_scope": build_execution_scope or {},
        "error": "；".join(errors),
        "clarification": {
            "mode": "build_task_plan_generation_error",
            "status": "failed",
            "message": "已绑定 Build Run 的任务计划发生变化，必须重新确认后启动新 Build。",
            "errors": errors,
        },
        "build_events": [*build_events, "scheduler:build_run_plan_changed"],
        "authorization_platform_projection_evidence": current_state.get(
            "authorization_platform_projection_evidence", {}
        ),
        "build_run_id": current_state.get("build_run_id"),
        "build_run_plan_path": current_state.get("build_run_plan_path"),
        "build_run_plan_sha256": current_state.get("build_run_plan_sha256"),
        "timeline": ["build"],
    }


def _build_gate_result(
    state: ProjectState,
    build_task_plan: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    """把待确认或平台门禁失败映射为 Build 节点可投影的结构。"""

    confirmation_status = build_task_plan.get("confirmation_status")
    pending = confirmation_status == "pending" and all(
        error == "Build DAG 尚未确认。" for error in errors
    )
    if pending:
        clarification = {
            "mode": "build_task_plan_confirmation",
            "status": "requires_user_input",
            "message": "Build DAG 已生成，请先确认最新任务规划。",
            "actionValues": ["confirm", "patch", "regenerate"],
            "editableFields": ["title", "description"],
            "errors": errors,
            "buildExecutionScope": build_task_plan.get("build_execution_scope") or state.get("build_execution_scope") or {},
        }
        workflow_status = "requires_user_input"
        summary_status = "requires_confirmation"
    else:
        clarification = {
            "mode": "build_task_plan_generation_error",
            "status": "failed",
            "message": "最新 Build DAG 未通过平台执行门禁，不能人工修正任务边界。",
            "errors": errors,
        }
        workflow_status = "failed"
        summary_status = "failed"
    tasks = tasks_from_build_task_plan(build_task_plan)
    return {
        "phase": "build",
        "status": workflow_status,
        "build_task_plan": build_task_plan,
        "tasks": tasks,
        "build_results": list(state.get("build_results", [])),
        "build_summary": {
            "status": summary_status,
            "total": len(tasks),
            "completed": 0,
            "failed": 0,
            "pending": len(tasks),
            "gate_errors": errors,
        },
        "build_execution_scope": state.get("build_execution_scope") or build_task_plan.get("build_execution_scope"),
        "error": "；".join(errors),
        "clarification": clarification,
        "build_events": ["scheduler:build_gate_blocked"],
        "timeline": ["build"],
    }


def _dedupe_build_gate_errors(errors: list[str]) -> list[str]:
    """按顺序去重 Build 门禁错误，保持任务或字段定位信息完整。"""

    result: list[str] = []
    for error in errors:
        text = str(error or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def run_build_scheduler(
    state: ProjectState,
    *,
    progress_writer: ProgressWriter | None = None,
) -> dict[str, Any]:
    """按 build_execution_scope 裁剪任务图，并持续调度到当前切片完成或阻塞。"""

    build_task_plan, build_run_binding, gate_errors = _bound_build_task_plan_for_build(state)
    if gate_errors:
        return _build_gate_result(state, build_task_plan, gate_errors)
    try:
        # 平台在 Agent 获取工作区快照前重放确认投影，源码差异单列为平台证据。
        authorization_platform_projection_evidence = (
            apply_authorization_platform_projections(
                workspace_from_state(state) or "",
                build_task_plan,
                build_run_id=build_run_binding.get("build_run_id"),
                plan_sha256=build_run_binding.get("build_run_plan_sha256"),
            )
        )
    except AuthorizationPlatformProjectionError as exc:
        blocked = _build_gate_result(
            state,
            build_task_plan,
            [f"权限共享投影写入失败，Build 已阻断：{exc}"],
        )
        return {
            **blocked,
            "authorization_platform_projection_evidence": {
                "status": "failed",
                "source": "platform.authorization_projection",
                "buildRunId": build_run_binding.get("build_run_id"),
                "planSha256": build_run_binding.get("build_run_plan_sha256"),
                "error": str(exc),
                "files": [],
                "summary": {"files": 0, "additions": 0, "deletions": 0},
            },
        }
    # 当前契约直接使用最新计划，不对历史 DAG 做运行时迁移或字段回填。
    canonical_tasks = list(state.get("tasks") or tasks_from_build_task_plan(build_task_plan))
    build_task_plan = replace_build_task_plan_tasks(build_task_plan, canonical_tasks)
    state = {
        **state,
        **build_run_binding,
        "build_task_plan_path": build_run_binding.get("build_run_plan_path"),
        "authorization_platform_projection_evidence": authorization_platform_projection_evidence,
        "build_results": hydrate_missing_failed_results(
            canonical_tasks,
            list(state.get("build_results", [])),
        ),
    }
    incoming_repair_task_plan = state.get("repair_task_plan")
    request = str(state.get("request") or "")
    database_paused_tasks = [
        task
        for task in canonical_tasks
        if isinstance(task, dict)
        and task.get("status") == "pending"
        and isinstance(task.get("scheduler"), dict)
        and task.get("scheduler", {}).get("paused_for") == "database_approval"
    ]
    database_approval_rejected = bool(database_paused_tasks) and any(
        signal in extract_confirmation_answer(request).replace(" ", "")
        for signal in ("拒绝", "不同意", "不批准")
    )
    if database_approval_rejected:
        return _database_approval_rejected_result(
            state=state,
            tasks=canonical_tasks,
            paused_tasks=database_paused_tasks,
            build_task_plan=build_task_plan,
        )
    retry_requested = bool(state.get("retry_failed_tasks"))
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
    retry_task_ids: set[str] = set()
    recovery_mode = ""
    recovery_task_ids: set[str] = set()
    if retry_requested:
        # 先在未追加修复任务的原始 DAG 上寻找瞬时失败；只有没有瞬时候选时，
        # 才把已有的 ready RepairPlanner 计划作为本次恢复入口，避免重跑旧修复任务。
        tasks = tasks_from_build_task_plan(build_task_plan)
        retry_slice = resolve_execution_slice(
            build_task_plan=build_task_plan,
            tasks=tasks,
            build_execution_scope=state.get("build_execution_scope"),
        )
        retry_task_ids = retryable_failed_task_ids(
            retry_slice["tasks"],
            list(state.get("build_results", [])),
        )
        if retry_task_ids:
            tasks = reset_failed_tasks_for_retry(tasks, retry_task_ids)
            build_task_plan = replace_build_task_plan_tasks(build_task_plan, tasks)
            recovery_mode = "retry"
        else:
            recovery_task_ids = ready_repair_task_ids(incoming_repair_task_plan)
            if recovery_task_ids:
                build_task_plan = append_repair_tasks_to_build_plan(
                    build_task_plan=build_task_plan,
                    repair_task_plan=incoming_repair_task_plan,
                    reset_existing_repair_tasks=True,
                )
                recovery_mode = "repair"
    elif ready_repair_task_ids(incoming_repair_task_plan):
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
    if retry_requested:
        if retry_task_ids:
            build_events.append(f"scheduler:retry:{','.join(sorted(retry_task_ids))}")
        elif recovery_task_ids:
            build_events.append(
                f"scheduler:retry:repair:{','.join(sorted(recovery_task_ids))}"
            )
        else:
            build_events.append("scheduler:retry:no_candidates")
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

        drift_errors = _build_run_plan_drift_errors(current_state)
        if drift_errors:
            return _build_run_plan_drift_result(
                current_state,
                errors=drift_errors,
                build_events=build_events,
                build_execution_scope=build_execution_scope,
            )

        ready_ids = selection["ready_task_ids"]
        build_events.append(f"scheduler:dispatch:{','.join(ready_ids)}")
        running_tasks = mark_tasks_running(current_state["tasks"], ready_ids)
        active_tool_activities: dict[str, dict[str, Any]] = {}
        tool_activity_lock = Lock()
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

            with tool_activity_lock:
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
        # 每个调度批次完成后刷新一次代码图，让后续阶段查询到真实的新文件、
        # 新符号和新关系；刷新失败只影响导航，不影响任务结果。
        refresh_code_graph_after_changes(
            workspace_from_state(current_state),
            code_change_sets,
        )
        database_approval = _database_approval_result(results)
        if database_approval is not None:
            paused_tasks = _reset_running_tasks_to_pending(running_tasks, ready_ids)
            approved_plan = database_approval.get("database_change_plan") or {}
            paused_tasks = [
                (
                    {**task, "approved_database_change_plan": approved_plan}
                    if task.get("id") in ready_ids
                    else task
                )
                for task in paused_tasks
            ]
            current_state = {
                **current_state,
                "tasks": paused_tasks,
                "build_task_plan": replace_build_task_plan_tasks(
                    current_state["build_task_plan"],
                    paused_tasks,
                ),
                "database_change_plan": database_approval.get("database_change_plan") or {},
                "database_approval_requests": [
                    *(
                        current_state.get("database_approval_requests")
                        if isinstance(current_state.get("database_approval_requests"), list)
                        else []
                    ),
                    database_approval.get("database_approval") or {},
                ],
            }
            build_events.append("scheduler:database_requires_approval")
            execution_slice = resolve_execution_slice(
                build_task_plan=current_state["build_task_plan"],
                tasks=current_state["tasks"],
                build_execution_scope=build_execution_scope,
            )
            build_summary = {
                **summarize_build_runtime(
                    execution_slice["tasks"],
                    _results_for_tasks(current_state.get("build_results", []), execution_slice["tasks"]),
                ),
                "status": "requires_confirmation",
            }
            return {
                **current_state,
                "phase": "build",
                "status": "requires_user_input",
                "build_summary": build_summary,
                "build_execution_scope": build_execution_scope,
                "build_execution_slice": execution_slice,
                "clarification": _database_approval_payload(database_approval),
                "build_events": build_events,
                "timeline": ["build"],
            }
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
            build_events.append(f"scheduler:repair_planned:{len(repair_task_plan['tasks'])}")
            _emit_build_progress(
                progress_writer,
                current_state=current_state,
                build_execution_scope=build_execution_scope,
                build_events=build_events,
                message=f"已生成修复任务：{len(repair_task_plan['tasks'])} 个。",
            )
            continue

        if summary["status"] == "requires_confirmation":
            build_events.append(f"scheduler:{summary['status']}")
            break
        # 失败任务不再立即终止调度循环：本批任务失败只阻塞其下游
        # （select_ready_build_batch 已排除依赖失败的任务），与失败任务无关的
        # 独立就绪任务应在下一轮继续并行调度。当 DAG 真正停滞（无就绪任务且
        # 无可推进的 pending）时，下一轮循环顶部的 `if not ready_tasks: break`
        # （scheduler:blocked）会自然退出，随后由最终的 build_summary 汇总出
        # failed 状态。这样前后端任务能真正并行，一个后端任务失败不会拖死
        # 与它无依赖关系的前端任务。
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
        repair_task_plan=repair_task_plan,
    )
    if retry_requested:
        build_summary.update(
            {
                "retry_requested": True,
                "retry_task_ids": sorted(retry_task_ids),
                "recovery_mode": recovery_mode or None,
                "recovery_task_ids": sorted(
                    recovery_task_ids or ready_repair_task_ids(repair_task_plan)
                ),
                **(
                    {
                        "retry_message": (
                            "当前没有可重试的构建任务，请调整计划或使用修复方案。"
                        )
                    }
                    if not retry_task_ids and not recovery_task_ids
                    else {}
                ),
            }
        )
    merged_code_changes = merge_code_change_sets(all_code_change_sets)
    workflow_status = (
        "completed"
        if build_summary.get("status") == "completed"
        else "requires_user_input"
        if build_summary.get("status") == "requires_confirmation"
        else "failed"
    )
    if workflow_status == "completed":
        edd_errors = verify_authorization_edd(
            workspace_from_state(state) or "",
            current_state.get("build_task_plan", build_task_plan),
        )
        if edd_errors:
            workflow_status = "failed"
            build_summary = {**build_summary, "status": "failed", "authorization_edd_errors": edd_errors}
    clarification = (
        _repair_scope_confirmation_payload(repair_task_plan)
        if isinstance(repair_task_plan, dict)
        and repair_task_plan.get("decision") == "requires_user_confirmation"
        else {}
    )

    return {
        "phase": "build",
        "project_plan": current_state.get("project_plan", state.get("project_plan", {})),
        "build_task_plan": current_state.get(
            "build_task_plan", state.get("build_task_plan", {})
        ),
        "build_task_plan_path": current_state.get(
            "build_run_plan_path", state.get("build_run_plan_path")
        ),
        **build_run_binding,
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
        "authorization_platform_projection_evidence": authorization_platform_projection_evidence,
        **code_change_state_update(merged_code_changes),
        "timeline": ["build"],
    }


def build(state: ProjectState) -> dict:
    """运行构建调度节点，并在 LangGraph 流中报告逐任务进度。"""

    return run_build_scheduler(state)
