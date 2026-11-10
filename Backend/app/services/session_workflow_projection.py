"""会话当前状态只读投影：冷进入不需要从历史消息寻找完整执行快照。"""

from pathlib import Path

from app.persistence.checkpoints import workflow_checkpoint_db_path
from app.persistence.execution_recovery import execution_recovery_db_path, get_latest_execution_for_thread
from app.protocols.workflow.projection import _workflow_summary, _workflow_visual_payload
from app.domain.execution_recovery import RecoveryExecutionError


async def read_session_recovery(workspace: str, *, session_id: str, thread_id: str):
    """会话恢复统一经过 checkpoint 解释器，停止和结束不能被旧确认字段重新打开。"""
    if not execution_recovery_db_path(workspace).is_file() or not workflow_checkpoint_db_path(workspace=workspace).is_file():
        return None, None, None
    execution = await get_latest_execution_for_thread(workspace, thread_id=thread_id)
    if execution is None or execution.owner_session_id != session_id:
        return None, None, None
    from app.services.execution_recovery import recovery_graph_for_execution
    from app.services.checkpoint_execution import interpret_checkpoint, checkpoint_source
    graph = await recovery_graph_for_execution(execution)
    try:
        snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
    except Exception as exc:
        raise RecoveryExecutionError("RECOVERY_CHECKPOINT_UNAVAILABLE", "暂时无法读取任务进度，原状态已保留，稍后将重新同步。") from exc
    values = dict(getattr(snapshot, "values", {}) or {})
    if (values.get("active_run_id") != execution.run_id or values.get("owner_session_id") != session_id
            or Path(str(values.get("workspace") or "")).resolve() != Path(workspace).resolve()):
        return None, snapshot, execution
    latest = await get_latest_execution_for_thread(workspace, thread_id=thread_id)
    if latest is None or latest.run_id != execution.run_id or latest.updated_at != execution.updated_at:
        raise RuntimeError("会话执行已变化，请重新同步。")
    if execution.execution_kind == "application_planning":
        from app.services.planning_recovery_projection import read_planning_recovery
        workflow, snapshot, _ = await read_planning_recovery(graph=graph, workspace=workspace,
            application_id=execution.project_id, thread_id=thread_id, snapshot=snapshot)
        return workflow, snapshot, execution
    interpreted = interpret_checkpoint(execution, snapshot, graph)
    execution = checkpoint_source(execution, interpreted)
    if interpreted is None:
        return None, snapshot, execution
    values = dict(interpreted.values)
    status = str(execution.status)
    values["status"] = "requires_user_input" if status == "awaiting_user" else status
    if status in {"cancelled", "stopped"}:
        values["message"] = "本轮任务已结束，已有文件已保留。"
    selecting = status == "completed" and (values.get("clarification") or {}).get("mode") == "development_target_selection"
    if status != "awaiting_user" and not selecting:
        values.pop("clarification", None)
        values.pop("application_planning_interrupt", None)
    if status in {"cancelled", "stopped", "interrupted"}:
        values.pop("revision_continuation", None)
        values.pop("development_continuation", None)
    for field in ("revision_continuation", "development_continuation"):
        continuation = values.get(field)
        if not isinstance(continuation, dict) or len(str(continuation.get("token") or "")) < 32:
            values.pop(field, None)
    if execution.workflow_scope == "conversation":
        from app.protocols.direct_modification_projection import direct_final_payload
        workflow = direct_final_payload(values, events=[])
        workflow.update(runId=execution.run_id, threadId=thread_id)
    else:
        workflow = _workflow_visual_payload(run_id=execution.run_id, thread_id=thread_id,
            summary=_workflow_summary(values, []), events=[], result=values, workspace=workspace)
    return workflow, snapshot, execution
