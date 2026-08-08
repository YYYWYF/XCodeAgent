from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.protocols.workflow.stream_events import (
    integration_test_check_summary,
    integration_test_checks,
)
from app.protocols.workflow.projection import _workspace_inspection_snapshot
from app.services.direct_modification import direct_state_message


DIRECT_NODE_LABELS = {
    "classify_intent": "识别对话意图",
    "scan_workspace_code": "扫描工作区代码",
    "respond_conversation": "生成对话回复",
    "answer_workspace": "读取工作区并回答",
    "execute_backend": "执行后端修改",
    "execute_frontend": "执行前端修改",
    "execute_workspace": "执行工作区修改",
    "integration_test": "验证项目",
    "direct_modification_repair": "自动修复局部代码",
    "launch_project": "启动本地预览",
    "finalize_direct_modification": "整理修改结果",
}
DIRECT_NODE_PERCENT = {
    "scan_workspace_code": 5,
    "classify_intent": 20,
    "respond_conversation": 80,
    "answer_workspace": 80,
    "execute_backend": 40,
    "execute_frontend": 65,
    "execute_workspace": 80,
    "integration_test": 80,
    "direct_modification_repair": 90,
    "launch_project": 95,
    "finalize_direct_modification": 100,
}


def direct_progress_payload(
    state: dict[str, Any],
    *,
    events: list[dict[str, Any]],
    process_step: dict[str, Any],
) -> dict[str, Any]:
    """构造可被现有会话组件消费的快速修改增量投影。"""

    status = str(state.get("status") or "in_progress")
    return {
        "summary": direct_summary(state, status=status),
        "events": events,
        "state": public_direct_state(state),
        "result": state.get("direct_modification_result", {}),
        "codeChanges": state.get("code_changes", {}),
        "processStep": process_step,
    }


def direct_final_payload(
    state: dict[str, Any],
    *,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造自定义事件、快照和 RunFinished 共用结果。"""

    status = str(state.get("status") or "failed")
    return {
        "status": status,
        "summary": direct_summary(state, status=status),
        "events": events,
        "state": public_direct_state(state),
        "result": state.get("direct_modification_result", {}),
        "codeChanges": state.get("code_changes", {}),
    }


def direct_summary(state: dict[str, Any], *, status: str) -> dict[str, Any]:
    """生成兼容现有 WorkflowRunPayload 展示层的快速修改摘要。"""

    result = state.get("direct_modification_result")
    result = result if isinstance(result, dict) else {}
    return {
        "status": status,
        "phase": str(state.get("phase") or "conversation"),
        "message": direct_state_message(state) or str(result.get("summary") or "自由对话已结束。"),
        "request": str(state.get("request") or ""),
        "previewUrl": state.get("preview_url") or result.get("previewUrl"),
        "launchResult": state.get("launch_result") or result.get("launchResult"),
        "clarification": (
            state.get("clarification")
            if isinstance(state.get("clarification"), dict)
            else None
        ),
        "owner": state.get("direct_modification_owner"),
        "scope": state.get("direct_modification_scope"),
        "intent": state.get("conversation_intent"),
        "repairIteration": state.get("repair_iteration", 0),
        "maxRepairIterations": state.get("max_repair_iterations", 3),
        "integrationNextAction": state.get("integration_next_action"),
    }


def public_direct_state(state: dict[str, Any]) -> dict[str, Any]:
    """裁剪快速修改 Graph State，避免把会话摘要和内部上下文发送到界面。"""

    keys = (
        "request",
        "phase",
        "status",
        "message",
        "conversation_intent",
        "conversation_response",
        "direct_modification_owner",
        "direct_modification_scope",
        "direct_modification_confidence",
        "direct_modification_reason",
        "clarification",
        "direct_stage_results",
        "backend_handoff",
        "test_results",
        "test_report_path",
        "quality_gate_passed",
        "repair_iteration",
        "max_repair_iterations",
        "repair_task_plan",
        "repair_tasks",
        "small_task_tasks",
        "small_task_results",
        "small_task_code_change_sets",
        "integration_next_action",
        "preview_url",
        "launch_result",
        "code_changes",
        "workspace_snapshot_summary",
        "workspace_revision",
        "workspace_scan_progress",
        "code_graph_index",
    )
    public_state = {key: state[key] for key in keys if key in state}
    inspection = _workspace_inspection_snapshot(state)
    if inspection is not None:
        public_state["workspaceInspection"] = inspection
    return public_state


def direct_node_event(
    node_name: str,
    *,
    update: dict[str, Any],
    run_id: str,
    thread_id: str,
) -> dict[str, Any]:
    """记录一个面向前端的快速修改节点完成事件。"""

    status = direct_node_status(node_name, update)
    label = DIRECT_NODE_LABELS.get(node_name, node_name)
    return {
        "type": "conversation.node.completed",
        "runId": run_id,
        "threadId": thread_id,
        "nodeName": node_name,
        "node": {"id": node_name, "label": label},
        "status": status,
        "message": direct_state_message(update) or f"已完成：{label}",
        "timestamp": datetime.now(UTC).isoformat(),
    }


def direct_node_started_event(
    node_name: str,
    *,
    run_id: str,
    thread_id: str,
) -> dict[str, Any]:
    """记录一个面向前端的快速修改节点开始事件。"""

    label = DIRECT_NODE_LABELS.get(node_name, node_name)
    return {
        "type": "conversation.node.started",
        "runId": run_id,
        "threadId": thread_id,
        "nodeName": node_name,
        "node": {"id": node_name, "label": label},
        "status": "running",
        "message": f"正在执行：{label}",
        "timestamp": datetime.now(UTC).isoformat(),
    }


def direct_node_running_process_step(node_name: str) -> dict[str, Any]:
    """把节点开始事件转换为可被 MessageList 原位更新的运行中步骤。"""

    label = DIRECT_NODE_LABELS.get(node_name, node_name)
    return {
        "id": f"direct:{node_name}",
        "kind": "workflow",
        "status": "running",
        "title": f"正在执行 {label}",
        "detail": f"正在执行：{label}",
        "sequence": DIRECT_NODE_PERCENT.get(node_name, 0),
        "nodeName": node_name,
    }


def direct_node_process_step(
    node_name: str,
    update: dict[str, Any],
) -> dict[str, Any]:
    """把节点终态转换为现有 MessageList 可展示的流程步骤。"""

    status = direct_node_status(node_name, update)
    label = DIRECT_NODE_LABELS.get(node_name, node_name)
    checks = (
        integration_test_checks(update.get("test_results", []))
        if node_name == "integration_test"
        else None
    )
    detail = direct_state_message(update)
    if node_name == "scan_workspace_code":
        graph = update.get("workspace_snapshot_summary")
        graph = graph.get("code_graph") if isinstance(graph, dict) else {}
        if isinstance(graph, dict) and graph.get("available"):
            detail = (
                f"代码图解析 {graph.get('filesIndexed', 0)} 个文件，"
                f"建立 {graph.get('symbolsIndexed', 0)} 个节点和 "
                f"{graph.get('relationsIndexed', 0)} 条关系"
            )
        else:
            detail = str(
                graph.get("message")
                if isinstance(graph, dict) and graph.get("message")
                else update.get("message") or "代码扫描完成。"
            )
    workspace_scan_progress = update.get("workspace_scan_progress")
    workspace_scan_progress = (
        workspace_scan_progress
        if isinstance(workspace_scan_progress, dict)
        else None
    )
    workspace_inspection = (
        _workspace_inspection_snapshot(update)
        if node_name == "scan_workspace_code"
        else None
    )
    return {
        "id": f"direct:{node_name}",
        "kind": "workflow",
        "status": status,
        "title": f"{'已完成' if status == 'completed' else '执行失败' if status == 'failed' else '等待输入'} {label}",
        "detail": integration_test_check_summary(checks) if checks else detail,
        "sequence": DIRECT_NODE_PERCENT.get(node_name, 0),
        "nodeName": node_name,
        **({"checks": checks} if checks else {}),
        **(
            {"workspaceInspectionProgress": workspace_scan_progress}
            if workspace_scan_progress is not None
            else {}
        ),
        **(
            {"workspaceInspection": workspace_inspection}
            if workspace_inspection is not None
            else {}
        ),
    }


def direct_node_status(node_name: str, update: dict[str, Any]) -> str:
    """把 Graph 节点业务状态映射为流程步骤终态。"""

    status = str(update.get("status") or "")
    if status == "failed":
        return "failed"
    if status in {"requires_user_input", "requires_planning"} and node_name != "launch_project":
        return "requires_user_input"
    return "completed"
