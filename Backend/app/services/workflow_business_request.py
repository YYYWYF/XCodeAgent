"""工作台结构化确认只控制执行，不替换原业务提问。"""

from typing import Any

from app.persistence.execution_recovery import get_execution


async def control_business_request(graph: Any, workspace: str, thread_id: str, values: dict) -> str:
    """从同一确认来源读取原问题；读取失败不阻塞主流程，原确认门继续负责合法性校验。"""

    controls = (
        "build_task_plan_confirmation", "test_phase_confirmation", "review_phase_confirmation",
        "acceptance_phase_confirmation", "unit_test_decision", "frontend_performance_decision",
        "code_review_repair_confirmation", "api_design_gate_action",
    )
    if not any(values.get(key) for key in controls) or not hasattr(graph, "aget_state"):
        return ""
    try:
        source_id = str(values.get("resume_execution_run_id") or "")
        source = await get_execution(workspace, source_id) if source_id and workspace else None
        source_thread = source.thread_id if source else thread_id
        snapshot = await graph.aget_state({"configurable": {"thread_id": source_thread}})
        state = snapshot.values if snapshot else {}
        if source_id and state.get("active_run_id") != source_id:
            return ""
        return str(state.get("request") or "")
    except Exception:
        return ""
