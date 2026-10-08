from __future__ import annotations

from typing import Any


def application_planning_interrupt_from_snapshot(snapshot: Any) -> dict[str, Any] | None:
    """从 LangGraph StateSnapshot 的挂起任务中读取创建规划审阅载荷。"""

    for task in getattr(snapshot, "tasks", ()) or ():
        for item in getattr(task, "interrupts", ()) or ():
            value = getattr(item, "value", None)
            if isinstance(value, dict) and value.get("type") == "application_planning_review":
                result = dict(value)
                clarification = result.get("clarification")
                if (
                    result.get("artifact") == "ui_designs"
                    and result.get("phase") == "ui_confirmation"
                    and (not isinstance(clarification, dict) or not clarification.get("mode"))
                    and isinstance(getattr(snapshot, "values", None), dict)
                ):
                    # 旧的非修改设计对话留下空 UI 门禁时，读取同一 checkpoint 的正式产物重建身份。
                    from app.graph.application_planning_interrupts import (
                        application_planning_review_payload,
                    )

                    restored = application_planning_review_payload(
                        snapshot.values, "ui_confirmation"
                    )
                    if restored.get("clarification", {}).get("mode") == "ui_design_confirmation":
                        result.update(
                            gateId=restored["gateId"],
                            artifactRevision=restored["artifactRevision"],
                            clarification=restored["clarification"],
                        )
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
        # 设计意图节点异常会消耗旧审阅门却来不及写入新门；恢复时必须显式
        # 展示失败，而不是把旧 checkpoint 的 requires_user_input 当成空确认卡。
        for task in getattr(snapshot, "tasks", ()) or ():
            if str(getattr(task, "name", "") or "") != "design_intent_analysis":
                continue
            failure = getattr(task, "error", None)
            if failure:
                return {
                    **result,
                    "phase": "design_intent_analysis",
                    "status": "failed",
                    "error": str(failure),
                    "clarification": {},
                    "application_planning_interrupt": {},
                }
        return result
    projected = {
        **result,
        "application_planning_interrupt": payload,
        # 普通产品回复仍保留正文，但当前可操作阶段必须以重新挂起的审阅门为准。
        "phase": str(payload.get("phase") or result.get("phase") or "requirements"),
        "status": "requires_user_input",
    }
    clarification = payload.get("clarification")
    if isinstance(clarification, dict):
        projected["clarification"] = clarification
    return projected
