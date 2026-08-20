from __future__ import annotations

from typing import Any


def application_planning_interrupt_from_snapshot(snapshot: Any) -> dict[str, Any] | None:
    """从 LangGraph StateSnapshot 的挂起任务中读取创建规划审阅载荷。"""

    for task in getattr(snapshot, "tasks", ()) or ():
        for item in getattr(task, "interrupts", ()) or ():
            value = getattr(item, "value", None)
            if isinstance(value, dict) and value.get("type") == "application_planning_review":
                result = dict(value)
                interrupt_id = str(getattr(item, "id", "") or "").strip()
                if interrupt_id:
                    result["interruptId"] = interrupt_id
                return result
    return None


def project_application_planning_interrupt(
    result: dict[str, Any],
    snapshot: Any,
) -> dict[str, Any]:
    """把原生中断投影回稳定 Workflow 状态，供确认卡和冷启动恢复共用。"""

    payload = application_planning_interrupt_from_snapshot(snapshot)
    if payload is None:
        return result
    current_phase = str(result.get("phase") or "")
    projected = {
        **result,
        "application_planning_interrupt": payload,
        "phase": (
            current_phase
            if current_phase == "design_chat_response"
            else str(payload.get("phase") or current_phase or "requirements")
        ),
        "status": "requires_user_input",
    }
    clarification = payload.get("clarification")
    if isinstance(clarification, dict):
        projected["clarification"] = clarification
    return projected
