from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.protocols.workflow.stream_events import (
    integration_test_check_summary,
    integration_test_checks,
)


DIRECT_NODE_LABELS = {
    "classify_intent": "识别修改意图",
    "execute_backend": "执行后端修改",
    "execute_frontend": "执行前端修改",
    "integration_test": "验证项目",
    "launch_project": "启动本地预览",
    "finalize_direct_modification": "整理修改结果",
}
DIRECT_NODE_PERCENT = {
    "classify_intent": 10,
    "execute_backend": 35,
    "execute_frontend": 60,
    "integration_test": 80,
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
        "phase": str(state.get("phase") or "direct_modification"),
        "message": str(state.get("message") or result.get("summary") or "快速修改已结束。"),
        "previewUrl": state.get("preview_url") or result.get("previewUrl"),
        "launchResult": state.get("launch_result") or result.get("launchResult"),
        # 快速澄清继续使用自由输入框，避免历史结构化确认卡在无 lifecycle 时重复提交。
        "clarification": None,
        "owner": state.get("direct_modification_owner"),
        "scope": state.get("direct_modification_scope"),
    }


def public_direct_state(state: dict[str, Any]) -> dict[str, Any]:
    """裁剪快速修改 Graph State，避免把会话摘要和内部上下文发送到界面。"""

    keys = (
        "phase",
        "status",
        "message",
        "direct_modification_owner",
        "direct_modification_scope",
        "direct_modification_confidence",
        "direct_modification_reason",
        "direct_stage_results",
        "backend_handoff",
        "test_results",
        "test_report_path",
        "quality_gate_passed",
        "preview_url",
        "launch_result",
        "code_changes",
    )
    return {key: state[key] for key in keys if key in state}


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
        "type": "direct-modification.node.completed",
        "runId": run_id,
        "threadId": thread_id,
        "nodeName": node_name,
        "node": {"id": node_name, "label": label},
        "status": status,
        "message": str(update.get("message") or f"已完成：{label}"),
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
        "type": "direct-modification.node.started",
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
    return {
        "id": f"direct:{node_name}",
        "kind": "workflow",
        "status": status,
        "title": f"{'已完成' if status == 'completed' else '执行失败' if status == 'failed' else '等待输入'} {label}",
        "detail": (
            integration_test_check_summary(checks)
            if checks
            else str(update.get("message") or "")
        ),
        "sequence": DIRECT_NODE_PERCENT.get(node_name, 0),
        "nodeName": node_name,
        **({"checks": checks} if checks else {}),
    }


def direct_node_status(node_name: str, update: dict[str, Any]) -> str:
    """把 Graph 节点业务状态映射为流程步骤终态。"""

    status = str(update.get("status") or "")
    if status == "failed":
        return "failed"
    if status in {"requires_user_input", "requires_planning"} and node_name != "launch_project":
        return "requires_user_input"
    return "completed"
