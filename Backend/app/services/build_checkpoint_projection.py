"""从当前 Run 已提交的 Build 进度补齐冷加载现场，不扫描其他执行或重跑任务。"""

from app.domain.execution_recovery import DurableExecutionStatus, RecoveryExecutionError


def build_checkpoint_values(source, snapshot, *, live: bool) -> dict:
    """只在已退出的 Build 边界读取同 Run 进度，保留用户结束、确认门与阶段错误。"""
    values = snapshot.values
    pending = list(getattr(snapshot, "next", ()) or ())
    tasks = getattr(snapshot, "tasks", ()) or ()
    errors = [task.name for task in tasks if getattr(task, "error", None)]
    if source.status is DurableExecutionStatus.INTERRUPTED and all(
        str(task.error) == "CancelledError()" for task in tasks if getattr(task, "error", None)
    ):
        errors = []
    position = (pending == ["build"] or errors == ["build"]
                or (not pending and values.get("phase") == "build"))
    if (live or not position or any(getattr(task, "interrupts", ()) for task in tasks)
            or source.execution_kind != "workbench"
            or source.workflow_scope == "conversation"
            or source.status in {DurableExecutionStatus.CANCELLED, DurableExecutionStatus.STOPPED}
            or values.get("status") in {"cancelled", "stopped"}
            or (values.get("plan_control") or {}).get("status") == "ended"):
        return values

    from app.workspace.task_documents import load_build_execution_record

    state = {**values, "workspace": source.workspace, "project_id": source.project_id}
    record = load_build_execution_record(state, source.run_id)
    if not record or record.get("progress") is None:
        return values
    if (record.get("execution_run_id") != source.run_id
            or record.get("thread_id") != source.thread_id
            or record.get("build_execution_scope") != values.get("build_execution_scope")):
        raise RecoveryExecutionError("BUILD_RETRY_SOURCE_DRIFT", "Build 进度与当前会话的执行身份不一致。")
    progress = record["progress"]
    if record.get("execution_stage") == "inherited":
        # 尚未提交任务的子 Run 不能把父 Run 的展示副本提升为自己的任务结果。
        return values
    if not isinstance(progress, dict):
        raise RecoveryExecutionError("BUILD_RETRY_RESULT_DRIFT", "Build 已提交进度不是有效的任务状态对象。")
    keys = ("build_run_id", "build_run_plan_path", "build_run_plan_sha256")
    if (record.get("progress_source_run_id") != source.run_id
            or not all(record.get(key) for key in keys)
            or any(record.get(key) != progress.get(key) for key in keys)):
        raise RecoveryExecutionError("BUILD_RETRY_BINDING_DRIFT", "Build 已提交进度缺少一致的当前运行绑定。")

    # 复用执行入口的身份、范围、计划摘要及任务合同校验；完整绑定确保此处只读已有副本。
    from app.graph.subgraphs.build import _restore_build_runtime
    from app.services.build_scheduler import resolve_execution_slice, summarize_build_runtime
    from app.services.build_task_waiting import task_retry_wait_summary

    restored = _restore_build_runtime(state, source.run_id, progress, retry_failed_tasks=False)
    sliced = resolve_execution_slice(build_task_plan=restored["build_task_plan"],
        tasks=restored["tasks"], build_execution_scope=restored.get("build_execution_scope"))
    summary = summarize_build_runtime(sliced["tasks"], restored.get("build_results", []),
        repair_task_plan=restored.get("repair_task_plan"))
    saved_summary = progress.get("build_summary") or {}
    if record.get("execution_stage") == "finalized":
        # 收尾失败及正式确认以节点真实结果为准，不能只凭任务计数变成成功或普通等待。
        summary = {**summary, **saved_summary}
    projected = {**restored, "phase": "build", "build_summary": summary}
    if values.get("status") == "completed" and pending == ["build"]:
        projected["status"] = "in_progress"
    if record.get("execution_stage") == "finalized" and saved_summary.get("status") == "failed":
        projected["status"] = "failed"
    if (not errors and not values.get("clarification") and values.get("status") != "failed"
            and source.status is not DurableExecutionStatus.FAILED
            and (record.get("execution_stage") != "finalized"
                 or saved_summary.get("status") == "awaiting_task_retry")
            and not summary.get("finalization_error") and not summary.get("gate_errors")):
        summary = task_retry_wait_summary(summary, sliced["tasks"], restored.get("build_results", []), [])
        if summary.get("status") == "awaiting_task_retry":
            projected.update(status="requires_user_input", clarification={}, error="",
                message=f"Build 等待处理：{summary.get('failed', 0)} 个任务失败，请在任务卡中重试。")
    sliced["summary"]["status"] = summary["status"]
    return {**projected, "build_summary": summary, "build_execution_slice": sliced}
