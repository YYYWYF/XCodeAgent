"""开发预览的独立 AG-UI 操作、日志订阅和修复确认协议。"""

import asyncio
from pathlib import Path
from threading import Event
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.protocols.ag_ui_action_stream import AgUiActionProgress, AgUiActionResult, build_ag_ui_action_stream
from app.protocols.workflow.run_control import workflow_run_registry
from app.services.application_lifecycle import load_application_lifecycle
from app.services.preview_runtime_guard import claim_maintenance, maintenance_lock, maintenance_owner, release_maintenance, pending_product_interaction
from app.services.preview_runtime_repair import execute_repair, load_repair, prepare_repair, repair_path, save_repair, source_digest
from app.services.preview_runtime_state import finish_attempt, read_record, runtime_snapshot
from app.services.project_launcher import launch_project_preview, stop_project_preview
from app.workspace.run_lease import workspace_run_leases


class PreviewRuntimeInput(BaseModel):
    """在协议边界约束工作区、动作和本次确认身份。"""
    model_config = ConfigDict(extra="forbid")
    workspace: str = Field(min_length=1, max_length=4096)
    action: Literal["get", "watch", "start", "restart", "stop", "diagnose", "confirm", "revise", "cancel"]
    attemptId: str = ""
    planId: str = ""
    feedback: str = Field(default="", max_length=4000)
    includeLogs: bool = True


_jobs: dict[tuple[str, str], asyncio.Task[Any]] = {}
_cancellations: dict[tuple[str, str], Event] = {}


def _job_finished(key: tuple[str, str], task: asyncio.Task[Any]) -> None:
    """移除已完成任务并消费断开订阅后的异常，避免后台引用泄漏。"""
    if _jobs.get(key) is task:
        _jobs.pop(key, None)
    if not task.cancelled():
        task.exception()


def preview_runtime_capabilities() -> dict[str, Any]:
    """发布当前独立预览协议。"""
    return {"name": "preview-runtime", "endpoint": "/preview-runtime/run", "transport": "ag-ui-sse", "actions": list(PreviewRuntimeInput.model_fields["action"].annotation.__args__), "customEventName": "preview-runtime", "stateSnapshotKey": "previewRuntime", "workflowIndependent": True}


def blocking_task(workspace: str, thread_id: str = "") -> dict[str, Any] | None:
    """合并真实运行与等待确认记录，不用旧健康记录推断任务状态。"""
    owner = maintenance_owner(workspace)
    if owner and owner["threadId"] != thread_id:
        return {**owner, "message": "当前应用有预览维护任务，请完成或停止后再操作。"}
    lifecycle = load_application_lifecycle(workspace)
    if lifecycle:
        initialization = getattr(lifecycle, "initialization", None)
        if initialization and str(initialization.status) in {"running", "stopping", "awaiting_user"}:
            return {"threadId": initialization.thread_id, "message": "当前应用的规划或模板任务尚未结束，请完成或明确停止后再操作。"}
        for execution in lifecycle.active_executions.values():
            if execution.thread_id != thread_id and str(execution.status) in {"running", "stopping", "awaiting_user"}:
                return {"threadId": execution.thread_id, "runId": execution.run_id, "message": "当前应用有任务执行中或等待确认，请完成或明确停止后再操作。"}
    pending = pending_product_interaction(workspace)
    if pending:
        return pending
    runs = workflow_run_registry.workspace_active_runs(workspace)
    if runs:
        return {"runId": runs[0], "message": "当前应用有任务正在执行，请结束后再操作。"}
    lease = workspace_run_leases.active_owner(workspace_root=workspace, project_id=None)
    if lease and lease.thread_id != thread_id:
        return {"threadId": lease.thread_id, "message": "当前应用有任务正在启动，请稍后重试。"}
    return None


def snapshot(workspace: str, thread_id: str, *, logs: bool = True) -> dict[str, Any]:
    """把运行事实、阻塞原因和当前会话修复投影到同一快照。"""
    return {"runtime": runtime_snapshot(workspace, logs=logs), "blockedBy": blocking_task(workspace), "repair": {key: value for key, value in load_repair(workspace, thread_id).items() if key not in {"tasks", "digest"}}}


async def run_mutation(request: PreviewRuntimeInput, thread_id: str, report: Any) -> dict[str, Any]:
    """持有维护占用完成操作，确认暂停期间保留占用。"""
    workspace = request.workspace
    key = (workspace, thread_id)
    cancel = _cancellations[key]
    repair: dict[str, Any] = {}
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    run_id = f"preview-runtime:{thread_id}"

    async def synchronous(function: Any, *args: Any, **kwargs: Any) -> Any:
        """停止时等待同步执行真正退出，再释放互斥与删除栅栏。"""
        worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancel.set()
            await asyncio.shield(worker)
            raise

    def progress(stage: str, message: str) -> None:
        """把同步启动器和 Agent 进度安全投递到事件循环。"""
        if cancel.is_set() and stage == "tool":
            raise RuntimeError("已请求停止修复，停止派发后续工具。")
        asyncio.run_coroutine_threadsafe(report(AgUiActionProgress(stage=stage, message=message, percent=0)), loop)

    try:
        if task is not None:
            workflow_run_registry.register(run_id, task, workspace=workspace, maintenance_thread_id=thread_id)
        if request.action in {"start", "restart", "stop"}:
            progress("restart", "正在停止旧服务并启动预览…" if request.action == "restart" else "正在更新预览服务…")
            if request.action == "stop":
                result = await synchronous(stop_project_preview, workspace)
                await synchronous(finish_attempt, workspace, result)
            else:
                result = await synchronous(launch_project_preview, workspace, force_restart=request.action == "restart", on_progress=lambda stage, status, message: progress(stage, message))
            return {**snapshot(workspace, thread_id), "launchResult": result}
        repair = load_repair(workspace, thread_id)
        if request.action == "confirm":
            if repair.get("status") != "awaiting_confirmation" or repair.get("planId") != request.planId:
                raise ValueError("修复确认已失效，请读取当前会话。")
            if repair.get("attemptId") != read_record(workspace).get("attemptId"):
                raise ValueError("启动记录已变化，请重新诊断。")
            markdown = repair_path(workspace, thread_id).with_suffix(".md").read_text(encoding="utf-8")
            if markdown != repair.get("markdown") or source_digest(workspace, repair["paths"]) != repair["digest"]:
                raise ValueError("修复计划或代码已被编辑，请重新诊断并确认。")
            repair = {**repair, "status": "running", "iteration": repair["iteration"] + 1}
            save_repair(workspace, thread_id, repair)
            progress("repair", f"正在执行第 {repair['iteration']}/3 轮修复…")
            repair = await synchronous(execute_repair, workspace, repair, progress, cancel)
        if not cancel.is_set() and (request.action in {"diagnose", "revise"} or repair.get("status") == "retry"):
            progress("diagnose", "正在结合本次启动日志诊断，并生成修复计划…")
            repair = await synchronous(prepare_repair, workspace, thread_id, repair, request.feedback)
        if cancel.is_set():
            repair.update(status="stopped", message="修复已停止，已产生的修改保留。")
        save_repair(workspace, thread_id, repair)
        return snapshot(workspace, thread_id)
    except asyncio.CancelledError:
        repair.update(status="stopped", message="修复已停止，已产生的修改保留。")
        if request.action in {"diagnose", "confirm", "revise"}:
            save_repair(workspace, thread_id, repair)
        raise
    except Exception as exc:
        if request.action in {"diagnose", "confirm", "revise"}:
            repair = {**repair, "status": "failed", "message": str(exc)}
            save_repair(workspace, thread_id, repair)
        raise
    finally:
        if repair.get("status") != "awaiting_confirmation":
            release_maintenance(workspace, thread_id)
        _cancellations.pop(key, None)
        workflow_run_registry.unregister(run_id, task)


def build_preview_runtime_stream(*, payload: dict[str, Any], accept: str | None = None) -> Any:
    """用公共 AG-UI 生命周期承载读取、维护与确认，不手写 SSE。"""
    async def operation(report: Any) -> AgUiActionResult:
        """先原子校验占用，再把持久操作与抽屉订阅生命周期分离。"""
        request = PreviewRuntimeInput.model_validate((payload.get("forwardedProps") or {}).get("previewRuntime") or {})
        root = Path(request.workspace).expanduser().resolve()
        if not (root / ".xcodeagent" / "application.json").is_file():
            raise ValueError("需要有效的应用工作区。")
        request.workspace = str(root)
        thread_id = str(payload.get("threadId") or "")
        if not thread_id:
            raise ValueError("预览操作必须包含会话身份。")
        key = (request.workspace, thread_id)
        if request.action in {"get", "watch"}:
            previous: dict[str, Any] = {}
            for _ in range(20 if request.action == "watch" else 1):
                current = await asyncio.to_thread(snapshot, request.workspace, thread_id, logs=request.includeLogs)
                if current != previous:
                    await report(AgUiActionProgress(stage="status", message="服务状态已更新", percent=0, data=current))
                    previous = current
                if request.action == "watch":
                    await asyncio.sleep(1)
            return AgUiActionResult(data=current, message="已读取服务状态。")
        if request.action == "cancel":
            owner = maintenance_owner(request.workspace)
            if not owner or owner["threadId"] != thread_id:
                raise ValueError("当前会话没有可停止的预览维护任务。")
            event = _cancellations.get(key)
            if event:
                event.set()
                repair = load_repair(request.workspace, thread_id)
                save_repair(request.workspace, thread_id, {**repair, "status": "stopping", "message": "已请求停止，等待当前操作安全结束。"})
                return AgUiActionResult(data=snapshot(request.workspace, thread_id), message="已请求停止，正在等待当前操作安全结束。")
            repair = load_repair(request.workspace, thread_id)
            save_repair(request.workspace, thread_id, {**repair, "status": "stopped", "message": "已停止修复，已有修改保留。"})
            release_maintenance(request.workspace, thread_id)
            return AgUiActionResult(data=snapshot(request.workspace, thread_id), message="已停止修复。")
        with maintenance_lock:
            blocker = blocking_task(request.workspace, thread_id)
            if blocker:
                raise ValueError(blocker["message"])
            owner = maintenance_owner(request.workspace)
            if owner and request.action not in {"confirm", "revise"}:
                raise ValueError("当前应用已有维护任务，请先完成或停止。")
            if key in _jobs and not _jobs[key].done():
                raise ValueError("当前操作仍在执行，请勿重复提交。")
            if request.action == "diagnose":
                current = runtime_snapshot(request.workspace, logs=False)
                if not current.get("repairAvailable") or not request.attemptId or current.get("attemptId") != request.attemptId:
                    raise ValueError("当前没有对应的启动失败记录，请先重启服务。")
                if load_repair(request.workspace, thread_id):
                    raise ValueError("本会话已有诊断记录，请使用继续操作。")
            if request.action in {"confirm", "revise"}:
                pending = load_repair(request.workspace, thread_id)
                if pending.get("status") != "awaiting_confirmation":
                    raise ValueError("当前会话没有待确认的修复计划。")
                if request.action == "confirm":
                    if pending.get("planId") != request.planId:
                        raise ValueError("修复计划确认已失效，请读取当前会话。")
                    if pending.get("attemptId") != read_record(request.workspace).get("attemptId"):
                        raise ValueError("启动记录已变化，请重新诊断。")
                    document = repair_path(request.workspace, thread_id).with_suffix(".md").read_text(encoding="utf-8")
                    if document != pending.get("markdown") or source_digest(request.workspace, pending["paths"]) != pending["digest"]:
                        raise ValueError("修复计划或代码已被编辑，请重新诊断并确认。")
            claim_maintenance(request.workspace, thread_id, request.action)
            _cancellations[key] = Event()
            job = asyncio.create_task(run_mutation(request, thread_id, report))
            _jobs[key] = job
            job.add_done_callback(lambda task: _job_finished(key, task))
        # 抽屉或订阅关闭不取消工作区操作；占用直到实际同步任务退出才释放。
        data = await asyncio.shield(job)
        data.update(snapshot(request.workspace, thread_id))
        return AgUiActionResult(data=data, message=str(data.get("repair", {}).get("message") or data.get("launchResult", {}).get("message") or "操作完成。"))

    return build_ag_ui_action_stream(payload=payload, event_name="preview-runtime", state_key="previewRuntime", run_id_prefix="preview-runtime", progress_operation=operation, error_message_prefix="预览服务操作失败", accept=accept)
