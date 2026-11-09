"""规划及会话共用的只读恢复快照；展示输出不能授权确认或覆盖用户结束事实。"""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.domain.execution_recovery import RecoveryExecutionError
from app.persistence.execution_streams import latest_execution_output
from app.services.application_lifecycle import load_application_lifecycle, application_lifecycle_payload


async def project_recovery_snapshot(*, workspace: str, application_id: str | None,
                                    thread_id: str, session_id: str | None = None,
                                    planning: bool = False, graph: Any = None) -> dict[str, Any]:
    """一次读取绑定应用、线程和会话，返回当前业务快照及独立的文字/过程展示。"""
    before = load_application_lifecycle(workspace)
    if before is not None and application_id and before.application.id != application_id:
        raise RecoveryExecutionError("RECOVERY_APPLICATION_CONFLICT", "当前工作区不属于请求的应用。")
    if planning and before is not None and before.initialization.thread_id and before.initialization.thread_id != thread_id:
        raise RecoveryExecutionError("RECOVERY_THREAD_CHANGED", "当前规划已切换到新的会话，请同步最新状态。")
    source = None
    if planning:
        from app.graph.application_planning_workflow import application_planning_graph_for_request
        from app.services.planning_recovery_projection import read_planning_recovery
        workflow, checkpoint, _ = await read_planning_recovery(graph=graph or application_planning_graph_for_request,
            workspace=workspace, application_id=application_id, thread_id=thread_id)
    else:
        from app.services.session_workflow_projection import read_session_recovery
        workflow, checkpoint, source = await read_session_recovery(workspace, session_id=session_id or "", thread_id=thread_id)
    from app.services.document_review_assets import find_document_review, document_review_is_current
    from app.services.recovery_checkpoint import checkpoint_thread_exists
    checkpoint_present = bool(getattr(checkpoint, "values", None)) or await checkpoint_thread_exists(workspace, thread_id)
    review = None
    identity = (getattr(checkpoint, "values", {}) or {}).get("document_review")
    stale_review = False
    try:
        if identity and not document_review_is_current(workspace, identity):
            # 仅在响应中撤销已过期的确认；重建仍通过明确 AG-UI 动作进行。
            workflow = None
            stale_review = True
        if not workflow and (not checkpoint_present or stale_review):
            # 已存在其他本机执行现场时不能将其草稿转移给当前新会话。
            owned_elsewhere = False
            if not planning and before:
                for item in before.active_executions.values():
                    if item.thread_id != thread_id and await checkpoint_thread_exists(workspace, item.thread_id):
                        owned_elsewhere = True
                        break
            candidate = None if owned_elsewhere else find_document_review(workspace, planning=planning)
            if candidate:
                review = {"status": "available", "kind": candidate.kind, "digest": candidate.digest}
    except (OSError, ValueError) as exc:
        # 重建后若文件被外部改坏，也只撤销旧确认并呈现内容问题，不冒充后端失联。
        workflow = None
        review = {"status": "blocked", "message": str(exc)}
    output = await latest_execution_output(workspace, thread_id, session_id)
    if output:
        # 输出只有文字和过程，统一恢复只以本轮 checkpoint 解释器的 Workflow 为权威。
        if workflow and output["runId"] != workflow["runId"]:
            output = None
    after = load_application_lifecycle(workspace)
    if before is not None and after is not None and before.revision != after.revision:
        raise RecoveryExecutionError("RECOVERY_SNAPSHOT_CHANGED", "任务状态正在更新，稍后将重新同步。")
    workflow = deepcopy(workflow)
    if workflow:
        # 外层只携带一份必要 lifecycle，防止 Workflow 与恢复快照形成重复或循环嵌套。
        workflow.get("state", {}).pop("lifecycle", None)
        workflow.get("summary", {}).pop("lifecycle", None)
        control = workflow.get("state", {}).get("planControl") or workflow.get("summary", {}).get("planControl") or {}
        if control.get("status") == "ended" or workflow.get("summary", {}).get("status") in {"cancelled", "stopped"}:
            # 结束回执不能再被同 Run 旧输出中的“请确认后继续”文字覆盖。
            output = None
    config = (getattr(checkpoint, "config", None) or {}).get("configurable", {})
    return {"applicationId": after.application.id if after else application_id,
        "threadId": thread_id, "sessionId": session_id, "kind": "planning" if planning else "session",
        "lifecycleRevision": after.revision if after else 0,
        "checkpointAvailable": checkpoint_present, "checkpointId": config.get("checkpoint_id"),
        "checkpointAt": getattr(checkpoint, "created_at", None),
        "executionUpdatedAt": source.updated_at.isoformat() if source else None,
        "startedAt": source.started_at.isoformat() if source else (output or {}).get("startedAt"),
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "workflow": workflow, "output": output, "documentReview": review}
