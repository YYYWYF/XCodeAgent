"""从当前 checkpoint 读取已完成续接的选择结果，不重放工作流或消费凭据。"""

from pathlib import Path
from typing import Any

from app.domain.application_lifecycle import ApplicationLifecycle, ApplicationLifecycleStage
from app.persistence.checkpoints import workflow_checkpoint_db_path, workflow_checkpointer
from app.persistence.execution_recovery import execution_recovery_db_path, get_latest_execution_for_thread
from app.protocols.workflow.projection import _workflow_summary, _workflow_visual_payload


async def project_workbench_result(
    workspace: str, state: ApplicationLifecycle, *, session_id: str, thread_id: str,
) -> dict[str, Any] | None:
    """只返回当前会话最新 Run 的开发目标选择，拒绝跨会话、旧 Run 和未完成结果。"""
    if (state.active_formal_revision is not None
            or state.initialization.stage != ApplicationLifecycleStage.READY_FOR_WORKBENCH
            or not execution_recovery_db_path(workspace).is_file()
            or not workflow_checkpoint_db_path(workspace=workspace).is_file()):
        return None
    execution = await get_latest_execution_for_thread(workspace, thread_id=thread_id)
    if (execution is None or execution.execution_kind != "workbench"
            or execution.status != "completed" or execution.owner_session_id != session_id):
        return None
    saver = await workflow_checkpointer(workspace=workspace)
    checkpoint = await saver.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
    values = checkpoint.checkpoint.get("channel_values", {}) if checkpoint else {}
    clarification = values.get("clarification")
    if (values.get("active_run_id") != execution.run_id
            or values.get("active_thread_id") != thread_id
            or values.get("owner_session_id") != session_id
            or Path(str(values.get("workspace") or "")).resolve() != Path(workspace).resolve()
            or values.get("status") != "completed"
            or values.get("phase") != "api_design_readiness_gate"
            or values.get("workflow_scope") == "conversation"
            or not isinstance(clarification, dict)
            or clarification.get("mode") != "development_target_selection"):
        return None
    # checkpoint 和索引分开读取；期间已启动下一轮时，旧结果不能重新成为可操作卡片。
    latest = await get_latest_execution_for_thread(workspace, thread_id=thread_id)
    if latest is None or latest.run_id != execution.run_id or latest.status != "completed":
        return None
    # 只恢复该结果所需字段，不公开 checkpoint 中旧确认、token 或其他业务产物。
    result = {key: values.get(key) for key in ("status", "phase", "message", "clarification")}
    workflow = _workflow_visual_payload(run_id=execution.run_id, thread_id=thread_id,
        summary=_workflow_summary(result, []), events=[], result=result)
    return {"runId": execution.run_id, "threadId": thread_id, "ownerSessionId": session_id,
            "startedAt": execution.started_at.isoformat(), "workflow": workflow}
