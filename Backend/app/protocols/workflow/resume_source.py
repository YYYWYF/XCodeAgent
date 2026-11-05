"""只按 Run/Thread 身份读取服务端 checkpoint，客户端展示资料不能成为继续输入。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.persistence.execution_recovery import get_execution, get_latest_execution_for_thread, execution_recovery_db_path
from app.persistence.checkpoints import workflow_checkpoint_db_path


class WorkflowResumeReference(BaseModel):
    """继续请求只携带原执行身份，不接收任何业务状态字段。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    runId: str = Field(min_length=1, max_length=512)
    threadId: str = Field(min_length=1, max_length=512)


def resume_reference(payload: dict[str, Any]) -> WorkflowResumeReference | None:
    """在协议入口拒绝完整前端快照；只识别当前身份引用契约。"""

    forwarded = payload.get("forwardedProps") or {}
    if not isinstance(forwarded, dict):
        raise ValueError("forwardedProps 必须为对象。")
    if any(key in source for source in (payload, forwarded) for key in ("resumeState", "resume_state")):
        raise ValueError("继续执行只接受 resumeReference，不能提交会话状态快照。")
    value = forwarded.get("resumeReference")
    return WorkflowResumeReference.model_validate(value) if value is not None else None


async def read_resume_source(graph: Any, payload: dict[str, Any], reference: WorkflowResumeReference) -> tuple[Any, dict[str, Any]]:
    """核对工作区、应用及原 Run 后读取同一 checkpoint；来源已被替换时拒绝继续。"""

    props = payload.get("forwardedProps") or {}
    application = props.get("application") or {}
    workspace = str(props.get("workspaceRoot") or payload.get("workspace") or "").strip()
    project_id = str(application.get("id") or props.get("projectId") or payload.get("projectId") or "").strip()
    if not workspace:
        raise ValueError("继续执行需要明确的工作区。")
    if not execution_recovery_db_path(workspace).is_file() or not workflow_checkpoint_db_path(workspace=workspace, project_id=project_id or None).is_file():
        raise ValueError("原任务没有可读取的后端恢复状态。")
    source = await get_execution(workspace, reference.runId)
    if source is None or source.thread_id != reference.threadId:
        raise ValueError("找不到匹配的服务端执行，请重新同步当前状态。")
    if Path(source.workspace).resolve() != Path(workspace).expanduser().resolve() or (project_id and source.project_id != project_id):
        raise ValueError("继续执行的来源不属于当前应用工作区。")
    latest = await get_latest_execution_for_thread(workspace, thread_id=reference.threadId)
    if latest is None or latest.run_id != reference.runId or source.status == "running":
        raise ValueError("原任务仍在运行或已被新运行替换，请同步后操作。")
    target = graph(workspace=workspace, project_id=project_id or None) if callable(graph) else graph
    if hasattr(target, "__await__"):
        target = await target
    if not hasattr(target, "aget_state"):
        raise ValueError("当前执行没有可读取的服务端 checkpoint。")
    saved = await target.aget_state({"configurable": {"thread_id": reference.threadId}})
    values = dict(getattr(saved, "values", {}) or {})
    if (values.get("active_run_id") != reference.runId
            or values.get("active_thread_id") not in (None, reference.threadId)
            or Path(str(values.get("workspace") or "")).resolve() != Path(workspace).expanduser().resolve()):
        raise ValueError("原任务状态已被其他运行替换，请重新同步后操作。")
    owner = str(props.get("sessionId") or "")
    answers = props.get("clarificationAnswers") or payload.get("clarificationAnswers") or {}
    # 跨阶段交接仍由既有结构化确认门验证；普通继续不能接管其他会话。
    handoff = any(key in answers for key in ("test_phase_confirmation", "review_phase_confirmation", "acceptance_phase_confirmation")) if isinstance(answers, dict) else False
    if owner and source.owner_session_id and owner != source.owner_session_id and not handoff:
        raise ValueError("原执行属于其他会话，不能在当前会话继续。")
    latest = await get_latest_execution_for_thread(workspace, thread_id=reference.threadId)
    if latest is None or latest.run_id != source.run_id or latest.updated_at != source.updated_at:
        raise ValueError("读取期间任务状态已变化，请重新同步。")
    # Graph reducer 管理的累计轨迹留在原 checkpoint，不能重复注入导致历史翻倍。
    for key in ("timeline", "build_events", "test_events", "code_review_events", "code_change_sets"):
        values.pop(key, None)
    return target, values
