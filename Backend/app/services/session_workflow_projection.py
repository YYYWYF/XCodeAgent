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
    saver = await workflow_checkpointer(workspace=workspace)
    saved = await saver.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
    values = saved.checkpoint.get("channel_values", {}) if saved else {}
    if (values.get("active_run_id") != execution.run_id or values.get("owner_session_id") != session_id
            or Path(str(values.get("workspace") or "")).resolve() != Path(workspace).resolve()):
        return None
    latest = await get_latest_execution_for_thread(workspace, thread_id=thread_id)
    if latest is None or latest.run_id != execution.run_id or latest.updated_at != execution.updated_at:
        return None
    values = dict(values)
    for field in ("revision_continuation", "development_continuation"):
        continuation = values.get(field)
        if not isinstance(continuation, dict) or len(str(continuation.get("token") or "")) < 32:
            values.pop(field, None)
    return _workflow_visual_payload(run_id=execution.run_id, thread_id=thread_id,
        summary=_workflow_summary(values, []), events=[], result=values, workspace=workspace)
