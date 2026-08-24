"""共享 SmallTask Agent 的任务包、并行批次和工作区差异校验。"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.agents.small_task import invoke_small_task_agent, normalize_small_task_result
from app.agents.tool_activity_stream import ToolActivityCallback
from app.services.engineering_acceptance_verifier import unauthorized_batch_paths
from app.services.small_task_scope import (
    SMALL_TASK_MAX_CONCURRENCY,
    _bounded_items,
    _path_matches_task,
    _string_list,
    _task_paths,
)
from app.workspace.code_changes import (
    build_code_change_set,
    diff_workspace_snapshots,
    snapshot_workspace,
)


def build_small_task_packet(
    task: dict[str, Any],
    state: dict[str, Any],
    *,
    source: str = "integration_test",
) -> dict[str, Any]:
    """构造有界任务包，把结构化工程与业务检查及失败证据交给 Agent。"""

    allowed_paths = _task_paths(task)
    return {
        "schemaVersion": "small-task-packet.v1",
        "source": source,
        "taskId": str(task.get("id") or ""),
        "kind": str(task.get("kind") or "repair"),
        "owner": str(task.get("owner") or "unknown"),
        "title": str(task.get("title") or task.get("description") or "局部代码修复")[:500],
        "description": str(task.get("description") or task.get("failure_reason") or "")[:4_000],
        "allowedPaths": allowed_paths[:100],
        "targetFiles": _string_list(task.get("target_files"), limit=100),
        "candidateFiles": _string_list(task.get("target_files"), limit=100),
        "changeScope": _bounded_items(task.get("change_scope"), limit=100),
        "engineeringAcceptanceChecks": _bounded_items(
            task.get("acceptance_checks") or task.get("engineering_acceptance_checks"),
            limit=40,
        ),
        "businessAcceptanceChecks": _bounded_items(
            task.get("business_acceptance_checks") or task.get("businessAcceptanceChecks"),
            limit=40,
        ),
        "businessAcceptanceEvidence": _bounded_items(
            task.get("business_acceptance_evidence") or task.get("businessAcceptanceEvidence"),
            limit=40,
        ),
        "businessAcceptanceSummary": _bounded_value(
            task.get("business_acceptance_summary") or task.get("businessAcceptanceSummary") or {},
            limit=2_000,
        ),
        "failureEvidence": _bounded_value(
            task.get("failure_evidence") or task.get("failureEvidence") or {},
            limit=6_000,
        ),
        "confirmedContext": {
            "buildExecutionScope": _bounded_value(
                state.get("build_execution_scope") or {},
                limit=1_500,
            ),
            "buildContext": _bounded_value(state.get("build_context") or {}, limit=4_000),
            "testReport": _bounded_value(state.get("test_report") or {}, limit=8_000),
            "revisionRequests": _bounded_value(
                state.get("revision_requests") or [],
                limit=6_000,
            ),
            "workspaceRevision": str(state.get("workspace_revision") or "")[:160],
        },
        "policies": {
            "noDatabaseSchemaOrDDL": True,
            "noFormalArtifactMutation": True,
            "noConfirmedContractChange": True,
            "maxTargetFiles": 100,
        },
    }


def execute_small_task_batch(
    *,
    state: dict[str, Any],
    tasks: list[dict[str, Any]],
    on_tool_activity: ToolActivityCallback | None = None,
    source: str = "integration_test.small_task",
) -> dict[str, Any]:
    """并行执行一个安全批次，并用批次前后快照确定真实改动和越权路径。"""

    workspace = str(state.get("workspace") or state.get("workspace_path") or "").strip() or None
    before = snapshot_workspace(workspace)
    results: list[dict[str, Any]] = []

    def run_one(task: dict[str, Any]) -> dict[str, Any]:
        """在线程中调用一个 SmallTask Agent，并保留异常为结构化失败。"""

        packet = build_small_task_packet(task, state, source=source)
        try:
            agent_note = invoke_small_task_agent(
                packet=packet,
                workspace=workspace,
                selected_skill_names=state.get("selected_skill_names"),
                on_tool_activity=(
                    _task_activity_callback(on_tool_activity, task)
                    if on_tool_activity is not None
                    else None
                ),
            )
            normalized = normalize_small_task_result(agent_note)
        except Exception as exc:
            normalized = {
                "status": "failed",
                "summary": "SmallTask Agent 执行异常。",
                "changedFiles": [],
                "verification": [],
                "alreadySatisfied": False,
                "failureReason": f"{type(exc).__name__}: {exc}"[:2_000],
                "escalation": {},
                "agentNote": "",
            }
        return {
            "taskId": str(task.get("id") or ""),
            "owner": str(task.get("owner") or ""),
            "status": normalized["status"],
            "summary": normalized["summary"],
            "changedFiles": normalized["changedFiles"],
            "verification": normalized["verification"],
            "alreadySatisfied": normalized["alreadySatisfied"],
            "failureReason": normalized["failureReason"],
            "escalation": normalized["escalation"],
            "agentNote": normalized["agentNote"],
            "packet": _packet_preview(packet),
        }

    with ThreadPoolExecutor(
        max_workers=max(1, min(len(tasks), SMALL_TASK_MAX_CONCURRENCY)),
        thread_name_prefix="small-task",
    ) as executor:
        futures = [
            executor.submit(contextvars.copy_context().run, run_one, task)
            for task in tasks
        ]
        for future in futures:
            results.append(future.result())

    after = snapshot_workspace(workspace)
    all_files = diff_workspace_snapshots(before, after, source_tool=source) if after else []
    unauthorized = unauthorized_batch_paths(
        {"files": all_files} if all_files else None,
        tasks,
    )
    change_sets: list[dict[str, Any]] = []
    for task, result in zip(tasks, results):
        task_files = [
            file_item
            for file_item in all_files
            if isinstance(file_item, dict)
            and _path_matches_task(str(file_item.get("path") or ""), task)
        ]
        result["changedFiles"] = [
            str(file_item.get("path") or "")
            for file_item in task_files
            if file_item.get("path")
        ]
        if result["status"] == "completed" and not task_files and not result["alreadySatisfied"]:
            result["status"] = "failed"
            result["failureReason"] = "Agent 报告完成，但授权范围内没有实际代码差异。"
        if unauthorized and result["status"] in {"completed", "already_satisfied"}:
            result["status"] = "failed"
            result["failureReason"] = (
                "检测到批次外文件变更：" + "、".join(sorted(set(unauthorized)))
            )[:2_000]
        if task_files and after:
            change_set = build_code_change_set(
                workspace_root=after.root,
                files=task_files,
                source_tool=source,
            )
            if change_set:
                result["codeChangeSet"] = change_set
                change_sets.append(change_set)
    return {
        "tasks": tasks,
        "results": results,
        "codeChangeSets": change_sets,
        "unauthorizedPaths": sorted(set(unauthorized)),
    }


def build_small_task_handoff(
    *,
    mode: str,
    reason: str,
    tasks: list[dict[str, Any]],
    escalation: dict[str, Any] | None = None,
    target_node: str = "",
) -> dict[str, Any]:
    """生成统一的用户确认载荷，支持范围确认和正式工作流升级两种模式。"""

    escalation = escalation if isinstance(escalation, dict) else {}
    requested_paths = _string_list(
        escalation.get("requestedPaths") or escalation.get("requested_paths"),
        limit=100,
    )
    task_ids = [str(task.get("id") or "") for task in tasks if task.get("id")]
    question = (
        f"小任务 Agent 请求扩大代码范围：{'、'.join(requested_paths) or '未提供具体路径'}。"
        f"原因：{reason or '当前任务范围不足'}。是否批准这些路径？"
        if mode == "small_task_scope_confirmation"
        else (
            f"当前小任务无法安全直接完成：{reason or '需要正式工作流处理'}。"
            f"建议转入“{target_node or 'development_readiness_gate'}”节点，是否确认？"
        )
    )
    return {
        "mode": mode,
        "status": "requires_user_input",
        "message": (
            "小任务需要确认新增代码范围。"
            if mode == "small_task_scope_confirmation"
            else "小任务需要升级到正式工作流。"
        ),
        "reason": reason[:2_000],
        "requestedPaths": requested_paths,
        "requestedResources": _bounded_items(
            escalation.get("requestedResources") or escalation.get("requested_resources"),
            limit=50,
        ),
        "workflowIntent": target_node,
        "taskIds": task_ids,
        "questions": [
            {
                "id": "small_task_handoff",
                "header": "小任务升级确认",
                "question": question,
                "type": "yesno",
                "allowOther": False,
            }
        ],
    }


def _task_activity_callback(
    callback: ToolActivityCallback,
    task: dict[str, Any],
) -> ToolActivityCallback:
    """为工具活动附加任务归属，便于 AG-UI 在并行执行时稳定展示。"""

    def report(activity: dict[str, Any]) -> None:
        """发送带 taskId 的单次工具活动。"""

        callback({**activity, "taskId": str(task.get("id") or "")})

    return report


def _packet_preview(packet: dict[str, Any]) -> dict[str, Any]:
    """保存可恢复的任务包摘要，不把完整日志复制进工作流状态。"""

    return {
        "taskId": packet.get("taskId"),
        "owner": packet.get("owner"),
        "allowedPaths": packet.get("allowedPaths", [])[:100],
        "engineeringAcceptanceChecks": packet.get("engineeringAcceptanceChecks", [])[:40],
        "businessAcceptanceChecks": packet.get("businessAcceptanceChecks", [])[:40],
        "businessAcceptanceSummary": packet.get("businessAcceptanceSummary", {}),
        "source": packet.get("source"),
    }


def _bounded_value(value: Any, *, limit: int) -> Any:
    """按 JSON 序列化长度限制日志和上下文，避免超过模型预算。"""

    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (dict, list)):
        text = str(value)
        return text[:limit]
    return value
