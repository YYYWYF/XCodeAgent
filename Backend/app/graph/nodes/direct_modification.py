from __future__ import annotations

from typing import Any, Callable

from langgraph.config import get_stream_writer

from app.agents.direct_modification import (
    answer_casual_conversation,
    answer_workspace_question,
    classify_direct_modification_intent,
    invoke_data_source_direct_modification,
    invoke_frontend_direct_modification,
    invoke_workspace_direct_modification,
    parse_direct_modification_agent_result,
)
from app.agents.tool_activity_stream import ToolActivityCallback
from app.graph.nodes.common import (
    capture_agent_file_changes,
    refresh_code_graph_after_changes,
    workspace_from_state,
)
from app.graph.nodes.lifecycle import launch_project
from app.graph.state import ProjectState
from app.graph.subgraphs.testing import integration_test
from app.services.direct_modification import (
    append_direct_conversation_summary,
    direct_final_message,
    direct_state_message,
    direct_test_log_paths,
    validated_dynamic_workspace_paths,
    validated_direct_stage_result,
)
from app.workspace.code_changes import CapturedWorkspaceChanges, merge_code_change_sets
from app.workspace.workspace_snapshot_documents import load_workspace_snapshot_json


def classify_direct_modification(state: ProjectState) -> dict[str, Any]:
    """识别快速修改归属，并在不安全时转为澄清或正式规划提示。"""

    request = str(state.get("request") or "").strip()
    decision = classify_direct_modification_intent(
        user_request=request,
        conversation_summary=str(state.get("direct_modification_summary") or ""),
        workspace_snapshot=_workspace_snapshot_for_classification(state),
        on_response_delta=_conversation_text_delta_writer(),
    )
    dynamic_workspace_paths = validated_dynamic_workspace_paths(
        workspace=workspace_from_state(state),
        request=request,
        owner=decision.owner,
        target_paths=decision.target_paths,
    )
    approved_paths = list(
        dict.fromkeys(
            [
                *state.get("direct_modification_approved_paths", []),
                *dynamic_workspace_paths,
            ]
        )
    )[:100]
    base: dict[str, Any] = {
        "phase": "classify_intent",
        "conversation_intent": decision.intent,
        "direct_modification_owner": decision.owner,
        "direct_modification_scope": decision.scope,
        "direct_modification_confidence": decision.confidence,
        "direct_modification_reason": decision.reason,
        "conversation_response": decision.response,
        "direct_stage_results": {},
        "direct_code_change_sets": [],
        "direct_modification_result": {},
        "direct_modification_target_paths": list(decision.target_paths),
        "direct_modification_approved_paths": approved_paths,
        "backend_handoff": {},
        "integration_contract_check_enabled": False,
        "integration_repair_enabled": False,
        "repair_iteration": max(0, int(state.get("repair_iteration", 0) or 0)),
        "max_repair_iterations": max(1, int(state.get("max_repair_iterations", 3) or 3)),
        "repair_task_plan": {},
        "repair_tasks": [],
        "small_task_tasks": [],
        "small_task_results": [],
        "small_task_code_change_sets": [],
        "small_task_handoff": {},
        "small_task_handoff_submission": {},
        "small_task_route": "",
        "test_results": [],
        "test_report": {},
        "test_report_path": "",
        "quality_gate_passed": False,
        "launch_result": {},
        "preview_url": "",
        "code_changes": {},
        "acceptance_request": {},
        "timeline": ["classify_intent"],
    }
    if state.get("direct_modification_handoff_decision") == "rejected":
        return {
            **base,
            "status": "failed",
            "message": "用户未批准 SmallTask Agent 的升级范围，本次自由对话修改已停止。",
            "clarification": {},
        }
    if decision.intent == "formal_workflow":
        message = "该需求超出小任务 Agent 的安全边界，需要确认后转入正式工作流。"
        return {
            **base,
            "status": "requires_user_input",
            "message": message,
            "clarification": {
                "mode": "small_task_workflow_handoff",
                "status": "requires_user_input",
                "message": message,
                "reason": decision.reason,
                "workflowIntent": "detail_confirmation",
                "questions": [
                    {
                        "id": "small_task_handoff",
                        "header": "正式工作流",
                        "question": "该需求可能涉及较大范围修改、确认过的契约或产品决策，是否进入正式工作流？",
                        "type": "yesno",
                        "allowOther": False,
                    }
                ],
            },
        }
    if decision.intent == "casual_chat" and decision.response:
        return {
            **base,
            "status": "completed",
            "message": decision.response,
            "conversation_response": decision.response,
            "clarification": {},
            "timeline": ["classify_intent"],
        }
    if decision.intent == "needs_clarification" or decision.owner == "unknown":
        question = decision.clarification_question
        return {
            **base,
            "status": "requires_user_input",
            "message": question,
            "clarification": {
                "mode": "direct_modification_clarification",
                "status": "requires_user_input",
                "message": question,
                "questions": [
                    {
                        "id": "direct_modification_clarification",
                        "header": "补充修改信息",
                        "question": question,
                        "type": "text",
                        "placeholder": "请描述具体功能、位置和期望结果。",
                    }
                ],
            },
        }
    return {
        **base,
        "status": "in_progress",
        "message": _classification_message(decision.intent, decision.owner),
        "clarification": {},
    }


def _workspace_snapshot_for_classification(state: ProjectState) -> dict[str, Any]:
    """读取扫描节点刚生成的完整快照，读取失败时退回安全摘要。"""

    snapshot_path = str(state.get("workspace_snapshot_path") or "").strip()
    if snapshot_path:
        try:
            snapshot = load_workspace_snapshot_json(snapshot_path)
            if isinstance(snapshot, dict):
                return snapshot
        except (OSError, ValueError, TypeError):
            pass
    summary = state.get("workspace_snapshot_summary")
    return summary if isinstance(summary, dict) else {}


def _direct_source_candidates(
    state: ProjectState,
    *,
    owner: str,
) -> list[str]:
    """从前置扫描快照提取源码候选，让执行 Agent 优先读取业务代码。"""

    snapshot = _workspace_snapshot_for_classification(state)
    section = snapshot.get("frontend" if owner == "frontend" else "backend")
    section = section if isinstance(section, dict) else {}
    keys = (
        ("pages", "components", "api_clients")
        if owner == "frontend"
        else ("api_routes", "models")
    )
    candidates: list[str] = []
    for key in keys:
        values = section.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            path = str(item.get("path") or "").strip() if isinstance(item, dict) else ""
            normalized = path.replace("\\", "/").lstrip("/")
            if not normalized or _is_generated_or_dependency_path(normalized):
                continue
            if normalized not in candidates:
                candidates.append(normalized)
            if len(candidates) >= 100:
                return candidates
    return candidates


def _is_generated_or_dependency_path(path: str) -> bool:
    """拒绝把依赖、缓存和构建产物作为自由对话源码候选。"""

    ignored = {
        ".next",
        ".turbo",
        ".venv",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "target",
    }
    return any(part.casefold() in ignored for part in path.split("/"))


def _classification_message(intent: str, owner: str) -> str:
    """为自由对话分类结果生成不暴露内部 Prompt 的简短状态。"""

    if intent == "casual_chat":
        return "已识别为常规对话。"
    if intent == "workspace_question":
        return "已识别为只读工作区问答。"
    return f"已识别为 {owner} 局部工作区修改。"


def respond_to_casual_conversation(state: ProjectState) -> dict[str, Any]:
    """使用无工具模型生成常规对话回复并直接形成可完成状态。"""

    response = answer_casual_conversation(
        user_request=str(state.get("request") or ""),
        conversation_summary=str(state.get("direct_modification_summary") or ""),
    )
    return {
        "phase": "respond_conversation",
        "status": "completed" if response else "failed",
        "message": response or "对话模型没有返回有效内容。",
        "conversation_response": response,
        "clarification": {},
        "timeline": ["respond_conversation"],
    }


def respond_to_workspace_question(state: ProjectState) -> dict[str, Any]:
    """调用只读工作区 Agent 回答需要工程证据的问题。"""

    response = answer_workspace_question(
        user_request=str(state.get("request") or ""),
        conversation_summary=str(state.get("direct_modification_summary") or ""),
        workspace=workspace_from_state(state),
        selected_skill_names=state.get("selected_skill_names"),
        on_tool_activity=_tool_activity_writer("answer_workspace"),
        on_text_delta=_conversation_text_delta_writer(),
    )
    return {
        "phase": "answer_workspace",
        "status": "completed" if response else "failed",
        "message": response or "工作区问答 Agent 没有返回有效内容。",
        "conversation_response": response,
        "clarification": {},
        "timeline": ["answer_workspace"],
    }


def execute_frontend_direct_modification(state: ProjectState) -> dict[str, Any]:
    """使用共用 Frontend Agent 和独立 Prompt 执行局部前端修改。"""

    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="direct_modification.frontend",
        action=lambda: invoke_frontend_direct_modification(
            user_request=str(state.get("request") or ""),
            conversation_summary=str(state.get("direct_modification_summary") or ""),
            backend_handoff=state.get("backend_handoff"),
            candidate_files=_direct_source_candidates(state, owner="frontend"),
            approved_paths=state.get("direct_modification_approved_paths"),
            workspace=workspace,
            selected_skill_names=state.get("selected_skill_names"),
            on_tool_activity=_tool_activity_writer("execute_frontend"),
        ),
        capture_exceptions=True,
    )
    stage_result = validated_direct_stage_result(
        _direct_result_from_capture(captured),
        code_change_set=captured.code_change_set,
        owner="frontend",
    )
    code_graph_index = refresh_code_graph_after_changes(
        workspace,
        [captured.code_change_set] if captured.code_change_set else [],
        on_progress=_code_graph_progress_writer("execute_frontend"),
    )
    return _stage_update(
        state,
        stage="frontend",
        phase="execute_frontend",
        stage_result=stage_result,
        code_change_set=captured.code_change_set,
        code_graph_index=code_graph_index,
    )


def execute_backend_direct_modification(state: ProjectState) -> dict[str, Any]:
    """使用共用 Data Source Agent 和独立 Prompt 执行局部后端修改。"""

    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="direct_modification.data_source",
        action=lambda: invoke_data_source_direct_modification(
            user_request=str(state.get("request") or ""),
            conversation_summary=str(state.get("direct_modification_summary") or ""),
            candidate_files=_direct_source_candidates(state, owner="backend"),
            approved_paths=state.get("direct_modification_approved_paths"),
            workspace=workspace,
            selected_skill_names=state.get("selected_skill_names"),
            on_tool_activity=_tool_activity_writer("execute_backend"),
        ),
        capture_exceptions=True,
    )
    stage_result = validated_direct_stage_result(
        _direct_result_from_capture(captured),
        code_change_set=captured.code_change_set,
        owner="backend",
    )
    code_graph_index = refresh_code_graph_after_changes(
        workspace,
        [captured.code_change_set] if captured.code_change_set else [],
        on_progress=_code_graph_progress_writer("execute_backend"),
    )
    handoff = dict(stage_result.get("backendHandoff") or {})
    handoff["changedFiles"] = list(stage_result.get("changedFiles") or [])
    return {
        **_stage_update(
            state,
            stage="backend",
            phase="execute_backend",
            stage_result=stage_result,
            code_change_set=captured.code_change_set,
            code_graph_index=code_graph_index,
        ),
        "backend_handoff": handoff,
    }


def execute_workspace_direct_modification(state: ProjectState) -> dict[str, Any]:
    """使用共享 SmallTask Agent 修改分类器明确给出的普通工作区路径。"""

    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="conversation.workspace_change",
        action=lambda: invoke_workspace_direct_modification(
            user_request=str(state.get("request") or ""),
            conversation_summary=str(state.get("direct_modification_summary") or ""),
            target_paths=list(state.get("direct_modification_target_paths", [])),
            approved_paths=state.get("direct_modification_approved_paths"),
            workspace=workspace,
            selected_skill_names=state.get("selected_skill_names"),
            on_tool_activity=_tool_activity_writer("execute_workspace"),
        ),
        capture_exceptions=True,
    )
    stage_result = validated_direct_stage_result(
        _direct_result_from_capture(captured),
        code_change_set=captured.code_change_set,
        owner="workspace",
    )
    update = _stage_update(
        state,
        stage="workspace",
        phase="execute_workspace",
        stage_result=stage_result,
        code_change_set=captured.code_change_set,
    )
    if update.get("status") == "in_progress":
        update["status"] = "completed"
    return update


def run_direct_modification_integration_test(state: ProjectState) -> dict[str, Any]:
    """复用集成测试节点，并把失败证据交给独立自由对话修复节点。"""

    repair_iteration = max(0, int(state.get("repair_iteration", 0) or 0))
    max_repair_iterations = max(
        1,
        int(state.get("max_repair_iterations", 3) or 3),
    )
    result = integration_test(
        {
            **state,
            "integration_contract_check_enabled": False,
            "integration_repair_enabled": False,
            "repair_iteration": repair_iteration,
            "max_repair_iterations": max_repair_iterations,
        }
    )
    passed = result.get("quality_gate_passed") is True
    revision_requests = [
        item
        for item in result.get("revision_requests", [])
        if isinstance(item, dict)
    ]
    can_repair = (
        not passed
        and bool(revision_requests)
        and repair_iteration < max_repair_iterations
    )
    test_sets = [
        item
        for item in result.get("code_change_sets", [])
        if isinstance(item, dict)
    ]
    return {
        **result,
        "phase": "integration_test",
        "status": "in_progress" if passed else "failed",
        "message": (
            "快速修改验证通过。"
            if passed
            else (
                f"快速修改验证失败，准备执行第 {repair_iteration + 1}/{max_repair_iterations} 轮自动修复。"
                if can_repair
                else (
                    f"快速修改验证失败，自动修复已达到 {max_repair_iterations} 轮上限。"
                    if revision_requests and repair_iteration >= max_repair_iterations
                    else "快速修改验证失败，请查看测试日志。"
                )
            )
        ),
        "revision_requests": revision_requests,
        "repair_iteration": repair_iteration,
        "max_repair_iterations": max_repair_iterations,
        "integration_next_action": (
            "launch_project" if passed else "direct_modification_repair" if can_repair else "handle_failure"
        ),
        "repair_task_plan": state.get("repair_task_plan", {}),
        "repair_tasks": state.get("repair_tasks", []),
        "small_task_tasks": state.get("small_task_tasks", []),
        "small_task_results": result.get(
            "small_task_results", state.get("small_task_results", [])
        ),
        "small_task_code_change_sets": result.get(
            "small_task_code_change_sets", state.get("small_task_code_change_sets", [])
        ),
        "direct_code_change_sets": [
            *state.get("direct_code_change_sets", []),
            *test_sets,
        ],
    }


def launch_direct_modification_project(state: ProjectState) -> dict[str, Any]:
    """复用项目启动节点，并保留其真实构建和启动证据。"""

    return launch_project(state)


def finalize_direct_modification(state: ProjectState) -> dict[str, Any]:
    """把各阶段结果合并为快速修改公开终态，并更新有界会话摘要。"""

    current_status = str(state.get("status") or "failed")
    launch_result = state.get("launch_result") if isinstance(state.get("launch_result"), dict) else {}
    conversation_intent = str(state.get("conversation_intent") or "workspace_change")
    is_answer = conversation_intent in {"casual_chat", "workspace_question"}
    if is_answer and current_status == "completed":
        status = "completed"
    elif launch_result and launch_result.get("status") != "failed":
        status = "completed"
    elif current_status == "requires_user_input":
        status = "requires_user_input"
    elif current_status == "requires_planning":
        status = "requires_planning"
    elif current_status == "failed" or launch_result.get("status") == "failed":
        status = "failed"
    else:
        status = current_status if current_status in {"completed", "failed"} else "failed"

    stage_results = _finalize_stage_results(
        state.get("direct_stage_results", {}),
        final_status=status,
    )
    stage_summaries = [
        str(item.get("summary") or "")
        for item in stage_results.values()
        if isinstance(item, dict) and str(item.get("summary") or "").strip()
    ]
    message = (
        str(state.get("conversation_response") or direct_state_message(state)).strip()
        if is_answer
        else direct_final_message(
            status=status,
            current_message=direct_state_message(state),
            stage_summaries=stage_summaries,
        )
    )
    code_changes = merge_code_change_sets(state.get("direct_code_change_sets", []))
    direct_result = {
        "status": status,
        "intent": conversation_intent,
        "owner": state.get("direct_modification_owner", "unknown"),
        "scope": state.get("direct_modification_scope", "needs_clarification"),
        "summary": message,
        "stageResults": stage_results,
        "codeChanges": code_changes or {},
        "tests": {
            "passed": state.get("quality_gate_passed") is True,
            "checks": state.get("test_results", []),
            "reportPath": state.get("test_report_path"),
        },
        "logPaths": direct_test_log_paths(state.get("test_results", [])),
        "launchResult": launch_result,
        "previewUrl": state.get("preview_url"),
        "repairIteration": state.get("repair_iteration", 0),
        "maxRepairIterations": state.get("max_repair_iterations", 3),
        "repairTaskPlan": state.get("repair_task_plan", {}),
        "repairTasks": state.get("repair_tasks", []),
        "smallTaskResults": state.get("small_task_results", []),
    }
    return {
        "phase": "conversation",
        "status": status,
        "message": message,
        "direct_modification_result": direct_result,
        "direct_stage_results": stage_results,
        "direct_modification_summary": append_direct_conversation_summary(
            str(state.get("direct_modification_summary") or ""),
            request=str(state.get("request") or ""),
            outcome=message,
        ),
        "code_changes": code_changes or {},
        "acceptance_request": {},
        "clarification": state.get("clarification", {}) if status == "requires_user_input" else {},
        "timeline": ["finalize_direct_modification"],
    }


def _stage_update(
    state: ProjectState,
    *,
    stage: str,
    phase: str,
    stage_result: dict[str, Any],
    code_change_set: dict[str, Any] | None,
    code_graph_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """合并单个 Agent 阶段结果和本轮权威代码差异。"""

    succeeded = stage_result.get("status") in {"completed", "already_satisfied"}
    has_partial_changes = (
        stage_result.get("partialChanges") is True
        and _code_change_set_has_files(code_change_set)
    )
    # 有真实落盘差异时，单次工具/模型异常只作为告警，继续交给独立验收节点判断。
    recoverable_failure = stage_result.get("status") == "failed" and has_partial_changes
    escalated = stage_result.get("status") in {
        "requires_user_confirmation",
        "requires_workflow",
    }
    change_sets = list(state.get("direct_code_change_sets", []))
    if code_change_set:
        change_sets.append(code_change_set)
    stage_status = (
        "requires_user_input"
        if escalated
        else "in_progress"
        if succeeded or recoverable_failure
        else "failed"
    )
    return {
        "phase": phase,
        "status": stage_status,
        "message": (
            "已保留已写入的修改，正在继续独立验收。"
            if recoverable_failure
            else stage_result.get("summary")
        ),
        "direct_stage_results": {
            **state.get("direct_stage_results", {}),
            stage: stage_result,
        },
        "direct_code_change_sets": change_sets,
        "code_changes": code_change_set or state.get("code_changes", {}),
        **({"code_graph_index": code_graph_index} if code_graph_index else {}),
        "clarification": (
            _direct_small_task_handoff(stage_result)
            if escalated
            else {}
        ),
        "timeline": [phase],
    }


def _direct_small_task_handoff(stage_result: dict[str, Any]) -> dict[str, Any]:
    """把自由对话 SmallTask Agent 的升级结果转换为确认卡。"""

    escalation = stage_result.get("escalation")
    escalation = escalation if isinstance(escalation, dict) else {}
    target = str(escalation.get("workflowIntent") or "detail_confirmation")
    reason = str(
        escalation.get("reason")
        or stage_result.get("summary")
        or "该修改需要正式工作流。"
    )[:2_000]
    return {
        "mode": "small_task_scope_confirmation"
        if stage_result.get("status") == "requires_user_confirmation"
        else "small_task_workflow_handoff",
        "status": "requires_user_input",
        "message": "自由对话修改需要确认后才能继续。",
        "reason": reason,
        "workflowIntent": target,
        "requestedPaths": escalation.get("requestedPaths", []),
        "requestedResources": escalation.get("requestedResources", []),
        "questions": [
            {
                "id": "small_task_handoff",
                "header": "修改升级确认",
                "question": (
                    f"该修改需要转入 {target} 节点处理。原因：{reason} 是否确认？"
                ),
                "type": "yesno",
                "allowOther": False,
            }
        ],
    }


def _direct_result_from_capture(captured: CapturedWorkspaceChanges) -> dict[str, Any]:
    """把 Agent 异常转换为带告警的阶段结果，同时保留已经产生的文件差异。"""

    if captured.error is None:
        return parse_direct_modification_agent_result(str(captured.value or ""))
    failure_reason = (
        f"{type(captured.error).__name__}: {captured.error}"
    )[:2_000]
    has_partial_changes = _code_change_set_has_files(captured.code_change_set)
    return {
        "status": "failed",
        "summary": (
            "快速修改 Agent 的某个工具调用中断，但已保留已写入的代码差异，正在继续验收。"
            if has_partial_changes
            else "快速修改 Agent 执行中断，未检测到已写入的代码差异。"
        ),
        "changedFiles": [],
        "verification": [],
        "alreadySatisfied": False,
        "failureReason": failure_reason,
        "partialChanges": has_partial_changes,
        "backendHandoff": {},
    }


def _code_change_set_has_files(code_change_set: dict[str, Any] | None) -> bool:
    """判断工作区快照是否捕获到至少一个真实文件差异。"""

    return bool(
        isinstance(code_change_set, dict)
        and any(
            isinstance(item, dict) and str(item.get("path") or "").strip()
            for item in code_change_set.get("files", [])
        )
    )


def _finalize_stage_results(
    raw_stage_results: Any,
    *,
    final_status: str,
) -> dict[str, dict[str, Any]]:
    """将已通过最终验收的部分失败阶段公开为成功，并保留原始工具告警。"""

    if not isinstance(raw_stage_results, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for stage, raw_item in raw_stage_results.items():
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        if final_status == "completed" and item.get("partialChanges") is True:
            original_summary = str(item.get("summary") or "").strip()
            item.update(
                {
                    "status": "completed",
                    "summary": "修改已落盘，并通过最终验收。",
                    "recoveredFromToolFailure": True,
                }
            )
            if original_summary:
                item["agentSummary"] = original_summary
        result[str(stage)] = item
    return result


def _tool_activity_writer(node_name: str) -> ToolActivityCallback:
    """把 Deep Agent 的安全化工具活动转发为 Graph custom stream。"""

    try:
        writer = get_stream_writer()
    except RuntimeError:
        writer = lambda _event: None

    def report(activity: dict[str, Any]) -> None:
        """发送一次带节点归属的工具活动。"""

        writer(
            {
                "type": "conversation.tool_activity",
                "node_name": node_name,
                "activity": activity,
            }
        )

    return report


def _code_graph_progress_writer(node_name: str) -> ToolActivityCallback:
    """把写入后的代码图刷新进度送入快速修改 AG-UI 流。"""

    try:
        writer = get_stream_writer()
    except RuntimeError:
        writer = lambda _event: None

    def report(progress: Any) -> None:
        """发送一条代码图刷新 custom 事件。"""

        detail = progress.as_dict() if hasattr(progress, "as_dict") else {}
        writer(
            {
                "type": "workspace_inspection.progress",
                "node_name": node_name,
                "message": str(detail.get("message") or "正在更新代码索引…"),
                "detail": detail,
            }
        )

    return report


def _conversation_text_delta_writer() -> Callable[[str], None] | None:
    """把模型文本增量写入 Graph custom stream，供 AG-UI 实时转发。"""

    try:
        writer = get_stream_writer()
    except RuntimeError:
        return None

    def report(delta: str) -> None:
        """发送一段不包含路由元数据的助手正文。"""

        if delta:
            writer({"type": "conversation.text_delta", "delta": delta})

    return report
