"""让当前 checkpoint 的上游确认门优先于旧任务草稿展示。"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def prioritize_checkpoint_confirmation(workspace: str, projection: dict[str, Any]) -> dict[str, Any]:
    """只调整响应中的确认归属，不修改产物、依赖哈希、执行状态或准入条件。"""
    if projection.get("source") != "pending_plan":
        return projection
    from app.graph.application_planning_workflow import application_planning_graph_for_request
    from app.protocols.application_planning_interrupt import application_planning_interrupt_from_snapshot
    from app.services.application_lifecycle import load_application_lifecycle

    lifecycle = load_application_lifecycle(workspace)
    if (lifecycle is None or not lifecycle.active_run_id or not lifecycle.initialization.thread_id
            or lifecycle.active_run_id == projection.get("workflowRunId")):
        return projection
    try:
        # 生命周期仅定位当前线程，是否存在新确认门必须由原生 checkpoint 证明。
        async with asyncio.timeout(2):
            graph = await application_planning_graph_for_request(workspace=workspace)
            snapshot = await graph.aget_state({"configurable": {
                "thread_id": lifecycle.initialization.thread_id, "checkpoint_ns": ""}})
        values = snapshot.values
        gate = application_planning_interrupt_from_snapshot(snapshot)
        if (values.get("active_run_id") != lifecycle.active_run_id
                or values.get("workflow_scope") != "application_planning"
                or values.get("owner_session_id") != projection.get("ownerSessionId")
                or gate is None):
            return projection
        # 新需求/UI/技术规划确认正在进行，旧任务文件仍可保留，但不能抢占当前会话。
        return {"schemaVersion": "planning-refresh.v1", "source": "none", "status": "idle",
                "message": "请先完成当前规划确认。"}
    except Exception:
        logger.warning("无法读取当前规划确认 checkpoint，保留原待确认投影。", exc_info=True)
        return projection
