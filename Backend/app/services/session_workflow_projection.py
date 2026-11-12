"""会话当前状态只读投影：冷进入不需要从历史消息寻找完整执行快照。"""

from pathlib import Path
from typing import Any

from app.persistence.checkpoints import workflow_checkpoint_db_path, workflow_checkpointer
from app.persistence.execution_recovery import execution_recovery_db_path, get_latest_execution_for_thread
from app.protocols.workflow.projection import _workflow_summary, _workflow_visual_payload


async def project_session_workflow(workspace: str, *, session_id: str, thread_id: str) -> dict[str, Any] | None:
    """只读取当前会话的最新 Run，交叉核对索引和 checkpoint 并拒绝过期结果。"""
    if not execution_recovery_db_path(workspace).is_file() or not workflow_checkpoint_db_path(workspace=workspace).is_file():
        return None
    execution = await get_latest_execution_for_thread(workspace, thread_id=thread_id)
    if execution is None or execution.owner_session_id != session_id:
        return None
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    if execution.execution_kind == "workbench":
        # 工作台仍读取原生现场，保留已提交 Build 进度的只读补齐，不恢复输出回放链路。
        from app.services.execution_recovery import recovery_graph_for_execution
        from app.services.checkpoint_execution import interpret_checkpoint, checkpoint_source

        graph = await recovery_graph_for_execution(execution)
        snapshot = await graph.aget_state(config)
        interpreted = interpret_checkpoint(execution, snapshot, graph)
        if interpreted is None:
            return None
        values = interpreted.values
    else:
        saver = await workflow_checkpointer(workspace=workspace)
        saved = await saver.aget_tuple(config)
        values = saved.checkpoint.get("channel_values", {}) if saved else {}
    if (values.get("active_run_id") != execution.run_id or values.get("owner_session_id") != session_id
            or Path(str(values.get("workspace") or "")).resolve() != Path(workspace).resolve()):
        return None
    latest = await get_latest_execution_for_thread(workspace, thread_id=thread_id)
    if latest is None or latest.run_id != execution.run_id or latest.updated_at != execution.updated_at:
        return None
    values = dict(values)
    if execution.execution_kind == "workbench":
        execution = checkpoint_source(execution, interpreted)
        status = execution.status.value
        values["status"] = "requires_user_input" if status == "awaiting_user" else status
        selecting = status == "completed" and (values.get("clarification") or {}).get("mode") == "development_target_selection"
        if status != "awaiting_user" and not selecting:
            values.pop("clarification", None)
            values.pop("application_planning_interrupt", None)
        if status in {"cancelled", "stopped", "interrupted"}:
            values.pop("revision_continuation", None)
            values.pop("development_continuation", None)
    if execution.status.value in {"cancelled", "stopped"}:
        values.update(status=execution.status.value, message="本轮任务已结束，已有文件已保留。")
        for field in ("clarification", "application_planning_interrupt", "revision_continuation", "development_continuation"):
            values.pop(field, None)
    for field in ("revision_continuation", "development_continuation"):
        continuation = values.get(field)
        if not isinstance(continuation, dict) or len(str(continuation.get("token") or "")) < 32:
            values.pop(field, None)
    return _workflow_visual_payload(run_id=execution.run_id, thread_id=thread_id,
        summary=_workflow_summary(values, []), events=[], result=values, workspace=workspace)
