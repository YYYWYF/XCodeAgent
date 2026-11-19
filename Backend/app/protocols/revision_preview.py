"""历史分支预览的独立 AG-UI 动作协议。

刻意**不复用** `preview-runtime/run`：那条流挂着维护锁、修复确认与 revise 状态机，
而历史分支预览是只读的，不需要修复/确认语义；把 revision 维度塞进去会牵连那些状态。
这里只做"物化该分支并启动 / 停止"这一件事。
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, AsyncIterator

from pydantic import BaseModel, ConfigDict, Field, UUID4

from app.protocols.ag_ui_action_stream import (
    AgUiActionProgress,
    AgUiActionResult,
    build_ag_ui_action_stream,
)
from app.services.revision_preview import (
    RevisionPreviewError,
    revision_preview_state,
    start_revision_preview,
    stop_revision_preview,
    stop_workspace_revision_previews,
)
from app.services.workspace_process_registry import workspace_process_registry

REVISION_PREVIEW_EVENT_NAME = "revision-preview"


class RevisionPreviewInput(BaseModel):
    """约束历史预览的工作区、分支和本轮身份。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    action: str
    workspace: str = Field(min_length=1, max_length=4096)
    revision: str = Field(min_length=1, max_length=1024)
    previewId: UUID4 | None = None


@dataclass(frozen=True)
class _PreviewStart:
    """保存尚未退出的启动和对应的受控进程组。"""

    revision: str
    process_run_id: str
    cancelled: Event
    task: asyncio.Task[dict[str, Any]]

    def cancel(self) -> None:
        """停止本轮 Git、Maven 和依赖安装进程，长驻服务由工作线程回收。"""

        self.cancelled.set()
        workspace_process_registry.cancel_run(self.process_run_id)


_running_starts: dict[str, dict[str, _PreviewStart]] = {}
_workspace_cleanups: dict[str, asyncio.Task[None]] = {}
_logger = logging.getLogger(__name__)


def schedule_revision_preview_cleanup(workspace: str) -> asyncio.Task[None]:
    """登记退出清理，要求已经提交的启动收口，再回收磁盘登记的历史服务。"""

    key = os.path.normcase(str(Path(workspace).expanduser().resolve()))
    pending = tuple(_running_starts.get(key, {}).values())
    for start in pending:
        start.cancel()
    if key in _workspace_cleanups:
        return _workspace_cleanups[key]

    async def cleanup() -> None:
        """等待启动线程退出后清理，避免迟到的 Java 或前端进程在欢迎页继续运行。"""

        try:
            if pending:
                await asyncio.wait([start.task for start in pending])
            await asyncio.to_thread(stop_workspace_revision_previews, key)
        except Exception:
            _logger.exception("退出工作台时停止历史预览失败：%s", key)
        finally:
            _workspace_cleanups.pop(key, None)

    task = asyncio.create_task(cleanup())
    _workspace_cleanups[key] = task
    return task


def revision_preview_capabilities() -> dict[str, Any]:
    """发布历史分支预览动作的公开协议能力。"""

    return {
        "name": "revision-preview",
        "endpoint": "/revision-preview/run",
        "transport": "ag-ui-sse",
        "actions": ["get", "start", "stop"],
        "previewIdRequiredFor": ["start", "stop"],
        "customEventName": REVISION_PREVIEW_EVENT_NAME,
        "stateSnapshotKey": "revisionPreview",
        "workflowIndependent": True,
    }


def build_revision_preview_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """按动作物化/启动/停止历史分支的预览，并发送完整 AG-UI 生命周期。"""

    inputs = _revision_preview_input(payload)
    action = str(inputs.get("action") or "")
    workspace = str(inputs.get("workspace") or "").strip()
    revision = str(inputs.get("revision") or "").strip()

    async def operation(report) -> AgUiActionResult:
        """在独立线程执行同步的物化/启动服务，通过队列转发进度。

        启动可能包含依赖安装（分钟级），必须放到线程里跑，否则会阻塞事件循环、
        连带卡住同一进程内的其它 AG-UI 流。
        """

        request = RevisionPreviewInput.model_validate(inputs)
        key = os.path.normcase(str(Path(request.workspace).expanduser().resolve()))
        if action == "get":
            state = await asyncio.to_thread(revision_preview_state, key, request.revision)
            return AgUiActionResult(
                data=_action_data(action, state), message="已读取历史分支预览状态。"
            )

        if action not in {"start", "stop"}:
            raise RevisionPreviewError("revisionPreview.action 必须是 get、start 或 stop。")
        if request.previewId is None:
            raise RevisionPreviewError("启动和停止历史预览需要提供 previewId。")
        preview_id = str(request.previewId)

        if action == "stop":
            pending = _running_starts.get(key, {}).get(preview_id)
            if pending is not None and pending.revision == revision:
                pending.cancel()
            result = await asyncio.to_thread(stop_revision_preview, key, revision, preview_id=preview_id)
            return AgUiActionResult(
                data=_action_data(action, result), message="历史分支预览已停止。"
            )

        loop = asyncio.get_running_loop()
        progress_queue: asyncio.Queue[tuple[str, str, int]] = asyncio.Queue()
        cancelled = Event()
        starts = _running_starts.setdefault(key, {})
        if preview_id in starts:
            raise RevisionPreviewError("该轮历史预览已经提交。")
        previous_starts = tuple(starts.values())
        for previous in previous_starts:
            previous.cancel()
        previous_cleanup = _workspace_cleanups.get(key)
        process_run_id = f"revision-preview:{key}:{preview_id}"

        def sync_report(stage: str, message: str, percent: int) -> None:
            """在阶段边界响应退出，并把同步进度转发到异步事件循环。"""

            if cancelled.is_set():
                raise RevisionPreviewError("历史分支预览启动已停止。")
            loop.call_soon_threadsafe(progress_queue.put_nowait, (stage, message, percent))

        async def drain_progress() -> None:
            """持续把队列中的进度转发为 AG-UI 进度事件，直到收到哨兵。"""

            while True:
                stage, message, percent = await progress_queue.get()
                if stage == "__done__":
                    return
                await report(AgUiActionProgress(stage=stage, message=message, percent=percent))

        def start_finished(start: asyncio.Task[dict[str, Any]]) -> None:
            """只在线程真正退出后解除登记，即使原 SSE 已断开也保留清理依据。"""

            starts.pop(preview_id)
            if not starts:
                _running_starts.pop(key)
            workspace_process_registry.allow_run(process_run_id)
            if not start.cancelled():
                start.exception()
            progress_queue.put_nowait(("__done__", "", 0))

        async def run_start() -> dict[str, Any]:
            """等待上一轮退出和当前工作区清理，全程保留可取消登记。"""

            blockers: list[asyncio.Task[Any]] = [previous.task for previous in previous_starts]
            if previous_cleanup is not None:
                blockers.append(previous_cleanup)
            if blockers:
                await asyncio.wait(blockers)
            if cancelled.is_set():
                raise RevisionPreviewError("历史分支预览启动已停止。")
            return await asyncio.to_thread(
                start_revision_preview, key, revision,
                preview_id=preview_id, process_run_id=process_run_id, report_progress=sync_report,
            )

        start = asyncio.create_task(run_start())
        starts[preview_id] = _PreviewStart(revision, process_run_id, cancelled, start)
        start.add_done_callback(start_finished)
        drain_task = None
        try:
            await report(AgUiActionProgress(
                stage="registered", message="已登记历史预览启动。", percent=0, data={"previewId": preview_id},
            ))
            drain_task = asyncio.create_task(drain_progress())
            # wait 不会随订阅取消启动线程，业务结果由已完成的 Future 单独取回。
            await asyncio.wait((start,))
            await drain_task
            result = start.result()
        finally:
            if drain_task is not None and not drain_task.done():
                drain_task.cancel()

        return AgUiActionResult(
            data=_action_data(action, result),
            message=f"分支 {revision} 的预览已启动。",
        )

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=REVISION_PREVIEW_EVENT_NAME,
        state_key="revisionPreview",
        run_id_prefix="revision-preview",
        progress_operation=operation,
        error_message_prefix="历史分支预览失败",
        error_data=lambda _exc: {"action": action, "revision": revision},
        accept=accept,
        workspace_root=workspace or None,
    )


def _action_data(action: str, result: dict[str, Any]) -> dict[str, Any]:
    """把服务返回的状态改名后再放进动作结果。

    `status` 是 AG-UI 信封自己的字段（completed / failed）：`build_ag_ui_action_stream`
    先写它、再展开动作数据，所以动作数据里的 `status` 会把它顶掉。而前端的载荷校验只认
    completed / failed，于是整条结果被判成无效、界面上报"没有返回有效的 AG-UI 状态"。
    预览服务的运行态是另一回事（running / idle / stopped），改名成 `previewStatus` 避让。
    """

    data = {"action": action, **result}
    if "status" in data:
        data["previewStatus"] = data.pop("status")
    return data


def _revision_preview_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 AG-UI forwardedProps 中提取历史分支预览的业务输入。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    value = forwarded_props.get("revisionPreview")
    return value if isinstance(value, dict) else {}
