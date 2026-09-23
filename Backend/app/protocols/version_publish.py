"""应用版本发布的独立 AG-UI 动作协议。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from app.protocols.ag_ui_action_stream import (
    AgUiActionProgress,
    AgUiActionResult,
    build_ag_ui_action_stream,
)
from app.services.version_publish import (
    VersionPublishRequest,
    publish_version,
)


VERSION_PUBLISH_EVENT_NAME = "version-publish"


def version_publish_capabilities() -> dict[str, Any]:
    """发布独立版本发布动作的公开协议能力。"""

    return {
        "name": "version-publish",
        "endpoint": "/version-publish/run",
        "transport": "ag-ui-sse",
        "actions": ["publish"],
        "customEventName": VERSION_PUBLISH_EVENT_NAME,
        "stateSnapshotKey": "versionPublish",
        "workflowIndependent": True,
    }


def build_version_publish_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """执行版本发布，并发送完整 AG-UI 生命周期与三步进度。"""

    version_publish_input = _version_publish_input(payload)

    async def operation(report) -> AgUiActionResult:
        """在独立线程执行同步发布服务，通过队列转发进度。"""

        loop = asyncio.get_running_loop()
        progress_queue: asyncio.Queue[tuple[str, str, int]] = asyncio.Queue()

        def sync_report(stage: str, message: str, percent: int) -> None:
            """把同步服务的进度回调转发到异步事件循环。"""

            asyncio.run_coroutine_threadsafe(
                progress_queue.put((stage, message, percent)), loop
            )

        async def drain_progress() -> None:
            """持续把队列中的进度转发为 AG-UI 进度事件，直到收到哨兵。"""

            while True:
                stage, message, percent = await progress_queue.get()
                if stage == "__done__":
                    return
                await report(
                    AgUiActionProgress(
                        stage=stage, message=message, percent=percent
                    )
                )

        drain_task = asyncio.create_task(drain_progress())

        request = VersionPublishRequest.model_validate(version_publish_input)

        def run_service() -> Any:
            return publish_version(request, report_progress=sync_report)

        try:
            result = await loop.run_in_executor(None, run_service)
        finally:
            # 通知 drain 协程退出。
            asyncio.run_coroutine_threadsafe(
                progress_queue.put(("__done__", "", 0)), loop
            )
            await drain_task

        data = result.model_dump(by_alias=True)
        message = f"已提交并推送到版本 {result.branch}。"
        return AgUiActionResult(data=data, message=message)

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=VERSION_PUBLISH_EVENT_NAME,
        state_key="versionPublish",
        run_id_prefix="version-publish",
        progress_operation=operation,
        error_message_prefix="版本发布失败",
        error_data=lambda _exc: {"action": "publish"},
        accept=accept,
        workspace_root=str(version_publish_input.get("workspaceRoot") or "") or None,
    )


def _version_publish_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 AG-UI forwardedProps 中提取版本发布业务输入。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    version_publish = forwarded_props.get("versionPublish")
    return version_publish if isinstance(version_publish, dict) else {}
