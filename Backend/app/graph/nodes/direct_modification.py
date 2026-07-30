from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer

from app.agents.direct_modification import (
    classify_direct_modification_intent,
    invoke_data_source_direct_modification,
    invoke_frontend_direct_modification,
    parse_direct_modification_agent_result,
)
from app.agents.tool_activity_stream import ToolActivityCallback
from app.graph.nodes.common import capture_agent_file_changes, workspace_from_state
from app.graph.nodes.lifecycle import launch_project
from app.graph.state import ProjectState
from app.graph.subgraphs.testing import integration_test
from app.services.direct_modification import (
    append_direct_conversation_summary,
    direct_final_message,
    direct_state_message,
    direct_test_log_paths,
    validated_direct_stage_result,
)
from app.workspace.code_changes import CapturedWorkspaceChanges, merge_code_change_sets


def classify_direct_modification(state: ProjectState) -> dict[str, Any]:
    """识别快速修改归属，并在不安全时转为澄清或正式规划提示。"""

    request = str(state.get("request") or "").strip()
    decision = classify_direct_modification_intent(
        user_request=request,
        conversation_summary=str(state.get("direct_modification_summary") or ""),
    )
    base: dict[str, Any] = {
        "phase": "classify_intent",
        "direct_modification_owner": decision.owner,
        "direct_modification_scope": decision.scope,
        "direct_modification_confidence": decision.confidence,
        "direct_modification_reason": decision.reason,
        "direct_stage_results": {},
        "direct_code_change_sets": [],
        "direct_modification_result": {},
        "backend_handoff": {},
        "integration_contract_check_enabled": False,
        "integration_repair_enabled": False,
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
    if decision.scope == "requires_planning":
        message = "该需求涉及较大范围的架构或产品决策，请使用正式设计工作流。"
        return {
            **base,
            "status": "requires_planning",
            "message": message,
        }
    if decision.scope == "needs_clarification" or decision.owner == "unknown":
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
        "message": f"已识别为 {decision.owner} 快速修改。",
        "clarification": {},
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
    return _stage_update(
        state,
        stage="frontend",
        phase="execute_frontend",
        stage_result=stage_result,
        code_change_set=captured.code_change_set,
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
    handoff = dict(stage_result.get("backendHandoff") or {})
    handoff["changedFiles"] = list(stage_result.get("changedFiles") or [])
    return {
        **_stage_update(
            state,
            stage="backend",
            phase="execute_backend",
            stage_result=stage_result,
            code_change_set=captured.code_change_set,
        ),
        "backend_handoff": handoff,
    }


def run_direct_modification_integration_test(state: ProjectState) -> dict[str, Any]:
    """复用集成测试节点，并关闭契约校验和自动修复规划。"""

    result = integration_test(
        {
            **state,
            "integration_contract_check_enabled": False,
            "integration_repair_enabled": False,
            "repair_iteration": 0,
            "max_repair_iterations": 0,
        }
    )
    passed = result.get("quality_gate_passed") is True
    test_sets = [
        item
        for item in result.get("code_change_sets", [])
        if isinstance(item, dict)
    ]
    return {
        **result,
        "phase": "integration_test",
        "status": "in_progress" if passed else "failed",
        "message": "快速修改验证通过。" if passed else "快速修改验证失败，请查看测试日志。",
        "integration_next_action": "launch_project" if passed else "handle_failure",
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
    if launch_result and launch_result.get("status") != "failed":
        status = "completed"
    elif current_status == "requires_user_input":
        status = "requires_user_input"
    elif current_status == "requires_planning":
        status = "requires_planning"
    elif current_status == "failed" or launch_result.get("status") == "failed":
        status = "failed"
    else:
        status = current_status if current_status in {"completed", "failed"} else "failed"

    stage_results = state.get("direct_stage_results", {})
    stage_summaries = [
        str(item.get("summary") or "")
        for item in stage_results.values()
        if isinstance(item, dict) and str(item.get("summary") or "").strip()
    ]
    message = direct_final_message(
        status=status,
        current_message=direct_state_message(state),
        stage_summaries=stage_summaries,
    )
    code_changes = merge_code_change_sets(state.get("direct_code_change_sets", []))
    direct_result = {
        "status": status,
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
    }
    return {
        "phase": "direct_modification",
        "status": status,
        "message": message,
        "direct_modification_result": direct_result,
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
) -> dict[str, Any]:
    """合并单个 Agent 阶段结果和本轮权威代码差异。"""

    succeeded = stage_result.get("status") == "completed"
    change_sets = list(state.get("direct_code_change_sets", []))
    if code_change_set:
        change_sets.append(code_change_set)
    return {
        "phase": phase,
        "status": "in_progress" if succeeded else "failed",
        "message": stage_result.get("summary"),
        "direct_stage_results": {
            **state.get("direct_stage_results", {}),
            stage: stage_result,
        },
        "direct_code_change_sets": change_sets,
        "code_changes": code_change_set or state.get("code_changes", {}),
        "timeline": [phase],
    }


def _direct_result_from_capture(captured: CapturedWorkspaceChanges) -> dict[str, Any]:
    """把 Agent 异常转换为失败阶段，同时保留异常前已经产生的文件差异。"""

    if captured.error is None:
        return parse_direct_modification_agent_result(str(captured.value or ""))
    failure_reason = (
        f"{type(captured.error).__name__}: {captured.error}"
    )[:2_000]
    return {
        "status": "failed",
        "summary": "快速修改 Agent 执行中断，已保留中断前的代码差异。",
        "changedFiles": [],
        "verification": [],
        "alreadySatisfied": False,
        "failureReason": failure_reason,
        "backendHandoff": {},
    }


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
                "type": "direct_modification.tool_activity",
                "node_name": node_name,
                "activity": activity,
            }
        )

    return report
