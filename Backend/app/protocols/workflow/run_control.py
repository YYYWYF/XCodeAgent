"""管理当前进程内活跃主工作流的注册与取消。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from threading import Lock
from typing import Any, AsyncIterator, Iterator, Literal

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
    load_application_lifecycle,
    stop_workbench_execution,
)
from app.services.build_task_plan_lifecycle import (
    AbandonPendingResult,
    abandon_pending_build_task_plan,
)
from app.services.planning_refresh_recovery import resolve_planning_refresh_state
from app.services.workspace_process_registry import workspace_process_registry

WorkflowCancellationStatus = Literal[
    "cancelled",
    "pending_confirmation",
    "not_running",
    "cancel_timeout",
]


class WorkflowRunRegistry:
    """当前进程内正在流式运行的主工作流任务注册表。

    该实现专门匹配单进程桌面后端；如果改为多 Worker 部署，必须替换成
    跨进程共享的取消协调机制。
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._tasks: dict[str, tuple[str, asyncio.Task[Any]]] = {}
        self._deleting_workspaces: set[str] = set()
        self._deleting_application_ids: dict[str, str] = {}

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
            workspace_process_registry.allow_run(run_id)

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
        if task and not task.done():
            workspace_process_registry.cancel_run(run_id)
        return bool(task and not task.done() and task.cancel())

    async def cancel_and_wait(
        self,
        run_id: str,
        *,
        timeout_seconds: float = 3.0,
        workspace: str | None = None,
    ) -> WorkflowCancellationStatus:
        """取消指定运行并等待终态；已提交 Pending 时让正常确认生命周期胜出。"""

        with self._lock:
            entry = self._tasks.get(run_id)
        task = entry[1] if entry is not None else None
        workspace_key = (
            entry[0]
            if entry is not None and entry[0]
            else _workspace_key(workspace)
        )
        if _pending_confirmation_for_run(workspace_key, run_id) is not None:
            return "pending_confirmation"
        if task is None or task.done():
            return "not_running"

        workspace_process_registry.cancel_run(run_id)
        cancellation_requested = task.cancel()
        if not cancellation_requested:
            return "not_running" if task.done() else "cancel_timeout"

        done, _pending = await asyncio.wait(
            [task],
            timeout=max(0.0, timeout_seconds),
        )
        if _pending_confirmation_for_run(workspace_key, run_id) is not None:
            # 极窄竞态下 Cancel 已发出但 Pending 先完成提交；恢复同步进程栅栏，
            # 后续 Confirm 仍可沿该 Workflow 身份进入正常 Build 生命周期。
            workspace_process_registry.allow_run(run_id)
            return "pending_confirmation"
        return "cancelled" if task in done or task.done() else "cancel_timeout"

    def is_active(self, run_id: str, *, workspace: str | None = None) -> bool:
        """判断指定 Workflow 是否仍由当前 Backend 进程真实持有。"""

        workspace_key = _workspace_key(workspace)
        with self._lock:
            entry = self._tasks.get(run_id)
        if entry is None or entry[1].done():
            return False
        return not workspace_key or entry[0] == workspace_key

    def begin_workspace_deletion(
        self,
        workspace: str,
        *,
        application_id: str | None = None,
    ) -> None:
        """建立工作区删除栅栏，并按需绑定发起稳定应用身份。"""

        workspace_key = _workspace_key(workspace)
        if not workspace_key:
            raise ValueError("删除应用必须提供有效的 workspaceRoot。")
        with self._lock:
            current_application_id = self._deleting_application_ids.get(workspace_key)
            if (
                current_application_id is not None
                and application_id is not None
                and current_application_id != application_id
            ):
                raise RuntimeError("工作区已有其他应用删除事务正在进行。")
            self._deleting_workspaces.add(workspace_key)
            if application_id is not None:
                self._deleting_application_ids[workspace_key] = application_id

    def end_workspace_deletion(self, workspace: str) -> None:
        """解除目标工作区删除栅栏及其绑定的应用身份。"""

        with self._lock:
            workspace_key = _workspace_key(workspace)
            self._deleting_workspaces.discard(workspace_key)
            self._deleting_application_ids.pop(workspace_key, None)

    def workspace_deletion_application_id(self, workspace: str) -> str | None:
        """返回当前删除事务绑定的应用标识；无绑定事务时返回空。"""

        with self._lock:
            return self._deleting_application_ids.get(_workspace_key(workspace))

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
                workspace_process_registry.cancel_run(_run_id)
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


def _pending_confirmation_for_run(
    workspace: str,
    run_id: str,
) -> dict[str, Any] | None:
    """只在有效 PendingPlan 精确归属于目标 Workflow 时返回权威确认投影。"""

    if not workspace:
        return None
    try:
        refresh = resolve_planning_refresh_state(
            workspace,
        )
    except (OSError, TypeError, ValueError):
        return None
    if (
        refresh.get("source") == "pending_plan"
        and refresh.get("status") == "awaiting_confirmation"
        and refresh.get("workflowRunId") == run_id
    ):
        return refresh
    return None


def build_workflow_cancellation_ag_ui_stream(
    *,
    thread_id: str,
    run_id: str,
    target_run_id: str,
    workspace: str = "",
    accept: str | None = None,
) -> AsyncIterator[str]:
    """通过正常的 AG-UI 事件流确认主工作流取消请求。"""

    encoder = EventEncoder(accept or "text/event-stream")
    message_id = f"cancel:{run_id}"

    async def stream() -> AsyncIterator[str]:
        status = await workflow_run_registry.cancel_and_wait(
            target_run_id,
            **({"workspace": workspace} if workspace else {}),
        )
        message = {
            "cancelled": "Workflow 已停止。",
            "pending_confirmation": (
                "Build DAG 已生成，当前进入待确认状态，请选择确认、重新生成或放弃。"
            ),
            "not_running": "目标 Workflow 已结束，无需停止。",
            "cancel_timeout": "Workflow 未能在限定时间内停止，请稍后重试。",
        }[status]
        lifecycle = (
            _planning_lifecycle_payload(workspace)
            if status == "pending_confirmation" and workspace
            else None
        )
        planning_refresh = (
            dict((lifecycle.get("extensions") or {}).get("planningRefresh") or {})
            if lifecycle is not None
            else _pending_confirmation_for_run(workspace, target_run_id) or {}
        )
        result = {
            "status": status,
            "targetRunId": target_run_id,
            "message": message,
            **(
                {"planningRefresh": planning_refresh}
                if status == "pending_confirmation"
                else {}
            ),
        }

        yield encoder.encode(RunStartedEvent(threadId=thread_id, runId=run_id))
        yield encoder.encode(TextMessageStartEvent(messageId=message_id, role="assistant"))
        if lifecycle is not None:
            # Pending 已成为权威结果时广播正常 lifecycle，不能只返回取消控制文案。
            yield encoder.encode(CustomEvent(name="application-lifecycle", value=lifecycle))
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
                lifecycle=_planning_lifecycle_payload(workspace),
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


def _planning_lifecycle_payload(workspace: str) -> dict[str, Any] | None:
    """读取当前 lifecycle，并附加不落盘的 authoritative Planning refresh 投影。"""

    lifecycle = load_application_lifecycle(workspace)
    if lifecycle is None:
        return None
    payload = application_lifecycle_payload(lifecycle)
    payload["extensions"] = {
        **dict(payload.get("extensions") or {}),
        "planningRefresh": resolve_planning_refresh_state(
            workspace,
        ),
    }
    return payload


def _build_abandon_frames(
    *,
    encoder: EventEncoder,
    thread_id: str,
    run_id: str,
    message_id: str,
    result: AbandonPendingResult,
    lifecycle: dict[str, Any] | None,
) -> Iterator[str]:
    """把 Pending Abandon 结果编码成完整 AG-UI 生命周期，不触发 Graph 或取消。"""

    messages = {
        "abandoned": "当前 Pending Build DAG 已安全放弃。",
        "already_abandoned": "该 Pending Build DAG 已经被放弃，请刷新查看当前状态。",
        "already_confirmed": "该 Build DAG 已经确认，不能再放弃。",
        "no_pending": "当前没有可放弃的 Pending Build DAG。",
        "stale_draft": "待放弃的 Build DAG 已变化，请刷新后基于当前版本操作。",
    }
    message = messages[result.status]
    public_result = {
        "status": result.status,
        "message": message,
        "errors": list(result.errors),
        **(
            {
                "planningRunId": result.draft_identity.planning_run_id,
                "draftDigest": result.draft_identity.draft_digest,
            }
            if result.draft_identity is not None
            else {}
        ),
    }
    workflow_status = (
        "completed"
        if result.status in {"abandoned", "no_pending"}
        else "requires_user_input"
    )
    workflow = {
        "runId": run_id,
        "threadId": thread_id,
        "summary": {
            "status": workflow_status,
            "phase": "build_task_plan_abandon",
            "message": message,
            **({"lifecycle": lifecycle} if lifecycle is not None else {}),
        },
        "events": [],
        "state": {
            "status": workflow_status,
            "phase": "build_task_plan_abandon",
            "buildTaskPlanAbandon": public_result,
            **({"lifecycle": lifecycle} if lifecycle is not None else {}),
        },
        "result": {
            "status": workflow_status,
            "phase": "build_task_plan_abandon",
            "buildTaskPlanAbandon": public_result,
            **({"lifecycle": lifecycle} if lifecycle is not None else {}),
        },
    }
    yield encoder.encode(RunStartedEvent(threadId=thread_id, runId=run_id))
    yield encoder.encode(TextMessageStartEvent(messageId=message_id, role="assistant"))
    if lifecycle is not None:
        # Abandon 成功或拒绝后都先广播 Backend 当前事实，前端不能按点击顺序猜测状态。
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
