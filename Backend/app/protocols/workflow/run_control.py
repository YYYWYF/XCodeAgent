"""管理当前进程内活跃主工作流的注册与取消。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from threading import Lock
from typing import Any, AsyncIterator, Iterator

from ag_ui.core import (
    CustomEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    StateSnapshotEvent,
)
from ag_ui.encoder import EventEncoder

from app.services.application_lifecycle import (
    application_lifecycle_payload,
    end_workbench_execution,
    stop_workbench_execution,
)
from app.services.build_task_plan_lifecycle import (
    AbandonPendingResult,
    abandon_pending_build_task_plan,
)


class WorkflowRunRegistry:
    """当前进程内正在流式运行的主工作流任务注册表。

    该实现专门匹配单进程桌面后端；如果改为多 Worker 部署，必须替换成
    跨进程共享的取消协调机制。
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._tasks: dict[str, tuple[str, asyncio.Task[Any]]] = {}
        self._deleting_workspaces: set[str] = set()

    def register(
        self,
        run_id: str,
        task: asyncio.Task[Any],
        *,
        workspace: str | None = None,
    ) -> None:
        """按规范工作区登记运行，并拒绝删除栅栏之后启动的新任务。"""

        workspace_key = _workspace_key(workspace)
        with self._lock:
            if workspace_key and workspace_key in self._deleting_workspaces:
                raise RuntimeError("当前应用正在删除，不能启动新的运行。")
            self._tasks[run_id] = (workspace_key, task)

    def unregister(self, run_id: str, task: asyncio.Task[Any] | None = None) -> None:
        """仅移除仍指向同一 asyncio task 的运行登记。"""

        with self._lock:
            current = self._tasks.get(run_id)
            if current is not None and (task is None or current[1] is task):
                self._tasks.pop(run_id, None)

    def cancel(self, run_id: str) -> bool:
        """向指定运行发出 asyncio 取消请求。"""

        with self._lock:
            entry = self._tasks.get(run_id)
        task = entry[1] if entry is not None else None
        return bool(task and not task.done() and task.cancel())

    def is_active(self, run_id: str, *, workspace: str | None = None) -> bool:
        """判断指定 Workflow 是否仍由当前 Backend 进程真实持有。"""

        workspace_key = _workspace_key(workspace)
        with self._lock:
            entry = self._tasks.get(run_id)
        if entry is None or entry[1].done():
            return False
        return not workspace_key or entry[0] == workspace_key

    def begin_workspace_deletion(self, workspace: str) -> None:
        """建立工作区删除栅栏，阻止清理期间出现新的运行。"""

        workspace_key = _workspace_key(workspace)
        if not workspace_key:
            raise ValueError("删除应用必须提供有效的 workspaceRoot。")
        with self._lock:
            self._deleting_workspaces.add(workspace_key)

    def end_workspace_deletion(self, workspace: str) -> None:
        """仅解除目标工作区的删除栅栏，供可逆停机阶段失败后恢复使用。"""

        with self._lock:
            self._deleting_workspaces.discard(_workspace_key(workspace))

    async def cancel_workspace(
        self,
        workspace: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """取消指定工作区任务并有限等待，超时任务交由删除协议拒绝后续清理。"""

        workspace_key = _workspace_key(workspace)
        current_task = asyncio.current_task()
        with self._lock:
            entries = [
                (run_id, task)
                for run_id, (registered_workspace, task) in self._tasks.items()
                if registered_workspace == workspace_key and task is not current_task
            ]
        requested_run_ids = [run_id for run_id, task in entries if not task.done()]
        for _run_id, task in entries:
            if not task.done():
                task.cancel()
        if entries:
            await asyncio.wait(
                [task for _run_id, task in entries],
                timeout=max(0.0, timeout_seconds),
            )
        with self._lock:
            remaining_run_ids = [
                run_id
                for run_id, (registered_workspace, task) in self._tasks.items()
                if registered_workspace == workspace_key
                and task is not current_task
                and not task.done()
            ]
        return {
            "requestedRunIds": requested_run_ids,
            "cancelledCount": len(requested_run_ids),
            "remainingRunIds": remaining_run_ids,
        }

    def is_workspace_deleting(self, workspace: str) -> bool:
        """返回指定工作区是否已经进入删除栅栏。"""

        with self._lock:
            return _workspace_key(workspace) in self._deleting_workspaces


workflow_run_registry = WorkflowRunRegistry()


def _workspace_key(workspace: str | None) -> str:
    """把显式工作区规范化为跨平台稳定的运行登记键。"""

    if not workspace or not str(workspace).strip():
        return ""
    resolved = Path(str(workspace)).expanduser().resolve(strict=False)
    return os.path.normcase(str(resolved))


def build_workflow_cancellation_ag_ui_stream(
    *,
    thread_id: str,
    run_id: str,
    target_run_id: str,
    accept: str | None = None,
) -> AsyncIterator[str]:
    """通过正常的 AG-UI 事件流确认主工作流取消请求。"""

    encoder = EventEncoder(accept or "text/event-stream")
    message_id = f"cancel:{run_id}"

    async def stream() -> AsyncIterator[str]:
        cancelled = workflow_run_registry.cancel(target_run_id)
        status = "cancel_requested" if cancelled else "not_running"
        message = (
            "已请求停止正在运行的 Workflow。"
            if cancelled
            else "目标 Workflow 已结束，无需停止。"
        )
        result = {
            "status": status,
            "targetRunId": target_run_id,
            "message": message,
        }

        yield encoder.encode(RunStartedEvent(threadId=thread_id, runId=run_id))
        yield encoder.encode(TextMessageStartEvent(messageId=message_id, role="assistant"))
        yield encoder.encode(TextMessageContentEvent(messageId=message_id, delta=message))
        yield encoder.encode(TextMessageEndEvent(messageId=message_id))
        yield encoder.encode(
            RunFinishedEvent(
                threadId=thread_id,
                runId=run_id,
                result={"workflowRunControl": result},
            )
        )

    return stream()


def build_workflow_plan_control_ag_ui_stream(
    *,
    action: str,
    workspace: str,
    target_run_id: str,
    planning_run_id: str = "",
    draft_digest: str = "",
    thread_id: str,
    run_id: str,
    accept: str | None = None,
) -> AsyncIterator[str]:
    """通过主 AG-UI 端点执行不启动 Graph 的计划控制动作。"""

    encoder = EventEncoder(accept or "text/event-stream")
    message_id = f"plan-control:{run_id}"

    async def stream() -> AsyncIterator[str]:
        if action not in {"stop", "end", "abandon"}:
            raise ValueError(f"不支持的计划控制动作：{action}")
        if action == "abandon":
            result = abandon_pending_build_task_plan(
                {"workspace": workspace},
                planning_run_id=planning_run_id,
                draft_digest=draft_digest,
            )
            for frame in _build_abandon_frames(
                encoder=encoder,
                thread_id=thread_id,
                run_id=run_id,
                message_id=message_id,
                result=result,
            ):
                yield frame
            return
        if not target_run_id:
            raise ValueError("计划控制动作缺少目标 runId。")
        lifecycle = application_lifecycle_payload(
            end_workbench_execution(workspace, run_id=target_run_id)
            if action == "end"
            else stop_workbench_execution(workspace, run_id=target_run_id)
        )
        message = (
            "计划已结束，工作区已恢复自由输入。"
            if action == "end"
            else "计划执行已暂停，可继续执行、调整计划或结束。"
        )
        workflow = {
            "runId": run_id,
            "threadId": thread_id,
            "summary": {
                "status": "cancelled",
                "phase": "plan_control",
                "message": message,
                "lifecycle": lifecycle,
            },
            "events": [],
            "state": {"status": "cancelled", "phase": "plan_control", "lifecycle": lifecycle},
            "result": {"status": "cancelled", "phase": "plan_control", "lifecycle": lifecycle},
        }
        yield encoder.encode(RunStartedEvent(threadId=thread_id, runId=run_id))
        yield encoder.encode(TextMessageStartEvent(messageId=message_id, role="assistant"))
        # 控制动作写入成功后立即广播生命周期，所有工作台区域共享同一 revision。
        yield encoder.encode(CustomEvent(name="application-lifecycle", value=lifecycle))
        yield encoder.encode(CustomEvent(name="workflow-run", value=workflow))
        yield encoder.encode(StateSnapshotEvent(snapshot={"workflow": workflow}))
        yield encoder.encode(TextMessageContentEvent(messageId=message_id, delta=message))
        yield encoder.encode(TextMessageEndEvent(messageId=message_id))
        yield encoder.encode(
            RunFinishedEvent(
                threadId=thread_id,
                runId=run_id,
                result={"workflow": workflow},
            )
        )

    return stream()


def _build_abandon_frames(
    *,
    encoder: EventEncoder,
    thread_id: str,
    run_id: str,
    message_id: str,
    result: AbandonPendingResult,
) -> Iterator[str]:
    """把 Pending Abandon 结果编码成完整 AG-UI 生命周期，不触发 Graph 或取消。"""

    messages = {
        "abandoned": "当前 Pending Build DAG 已安全放弃。",
        "no_pending": "当前没有可放弃的 Pending Build DAG。",
        "stale_draft": "待放弃的 Build DAG 已变化，请刷新后基于当前版本操作。",
    }
    message = messages[result.status]
    public_result = {
        "status": result.status,
        "message": message,
        "errors": list(result.errors),
    }
    workflow_status = "completed" if result.status in {"abandoned", "no_pending"} else "requires_user_input"
    workflow = {
        "runId": run_id,
        "threadId": thread_id,
        "summary": {
            "status": workflow_status,
            "phase": "build_task_plan_abandon",
            "message": message,
        },
        "events": [],
        "state": {
            "status": workflow_status,
            "phase": "build_task_plan_abandon",
            "buildTaskPlanAbandon": public_result,
        },
        "result": {
            "status": workflow_status,
            "phase": "build_task_plan_abandon",
            "buildTaskPlanAbandon": public_result,
        },
    }
    yield encoder.encode(RunStartedEvent(threadId=thread_id, runId=run_id))
    yield encoder.encode(TextMessageStartEvent(messageId=message_id, role="assistant"))
    yield encoder.encode(CustomEvent(name="workflow-run", value=workflow))
    yield encoder.encode(StateSnapshotEvent(snapshot={"workflow": workflow}))
    yield encoder.encode(TextMessageContentEvent(messageId=message_id, delta=message))
    yield encoder.encode(TextMessageEndEvent(messageId=message_id))
    yield encoder.encode(
        RunFinishedEvent(
            threadId=thread_id,
            runId=run_id,
            result={"workflow": workflow},
        )
    )
