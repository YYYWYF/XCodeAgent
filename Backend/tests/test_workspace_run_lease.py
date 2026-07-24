from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

from app.protocols.workflow import build_workflow_ag_ui_stream
from app.workspace.run_lease import (
    WorkspaceRunLeaseRegistry,
    workspace_run_leases,
)


class RecordingGraph:
    def __init__(self) -> None:
        """初始化 Graph 调用次数。"""

        self.calls = 0

    async def astream(self, initial_state, *, config, stream_mode):
        """记录流式调用并返回空事件流。"""

        self.calls += 1
        if False:
            yield {}

    async def aget_state(self, config):
        """返回可结束 AG-UI 流的最小完成状态。"""

        return SimpleNamespace(values={"status": "completed"})


class FailingGraph:
    async def astream(self, initial_state, *, config, stream_mode):
        """模拟 Graph 在开始执行后抛错。"""

        raise RuntimeError("graph failed")
        if False:
            yield {}


class WaitingGraph:
    def __init__(self) -> None:
        """初始化用于取消场景的开始与释放信号。"""

        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def astream(self, initial_state, *, config, stream_mode):
        """保持流运行，直到测试主动释放或取消。"""

        self.started.set()
        await self.release.wait()
        if False:
            yield {}


class WorkspaceRunLeaseTests(unittest.TestCase):
    def test_different_pages_with_shared_api_are_both_registered(self) -> None:
        """页面共享 API 时仍允许并行登记两个活动运行。"""

        registry = WorkspaceRunLeaseRegistry()
        with tempfile.TemporaryDirectory() as workspace:
            first = registry.acquire(
                workspace_root=workspace,
                project_id=None,
                execution_scope={"type": "page", "targetId": "orders"},
                resource_claims=[
                    {"type": "page", "targetId": "orders"},
                    {"type": "api_contract", "targetId": "orders-api"},
                ],
                thread_id="thread-orders",
                run_id="run-orders",
            )
            second = registry.acquire(
                workspace_root=workspace,
                project_id=None,
                execution_scope={"type": "page", "targetId": "search"},
                resource_claims=[
                    {"type": "page", "targetId": "search"},
                    {"type": "api_contract", "targetId": "orders-api"},
                ],
                thread_id="thread-search",
                run_id="run-search",
            )
            first.release()
            second.release()

    def test_same_page_and_application_scopes_do_not_block_registration(self) -> None:
        """同页面和应用级活动运行也只登记，不作为并发门禁。"""

        registry = WorkspaceRunLeaseRegistry()
        with tempfile.TemporaryDirectory() as workspace:
            first = registry.acquire(
                workspace_root=workspace,
                project_id=None,
                execution_scope={"type": "page", "targetId": "page-a"},
                thread_id="thread-a",
                run_id="run-a",
            )
            second = registry.acquire(
                workspace_root=workspace,
                project_id=None,
                execution_scope={"type": "page", "targetId": "page-b"},
                thread_id="thread-b",
                run_id="run-b",
            )
            same_page = registry.acquire(
                workspace_root=workspace,
                project_id=None,
                execution_scope={"type": "page", "targetId": "page-a"},
                thread_id="thread-c",
                run_id="run-c",
            )
            application = registry.acquire(
                workspace_root=workspace,
                project_id=None,
                execution_scope={"type": "application", "targetId": "application"},
                thread_id="thread-app",
                run_id="run-app",
            )
            first.release()
            second.release()
            same_page.release()
            application.release()

    def test_same_and_different_workspaces_are_all_registered(self) -> None:
        """工作区相同与否都不影响运行登记。"""

        registry = WorkspaceRunLeaseRegistry()
        with (
            tempfile.TemporaryDirectory() as first_workspace,
            tempfile.TemporaryDirectory() as second_workspace,
        ):
            first = registry.acquire(
                workspace_root=first_workspace,
                project_id=None,
                thread_id="thread-1",
                run_id="run-1",
            )
            same_workspace = registry.acquire(
                workspace_root=str(Path(first_workspace) / "."),
                project_id=None,
                thread_id="thread-2",
                run_id="run-2",
            )
            second = registry.acquire(
                workspace_root=second_workspace,
                project_id=None,
                thread_id="thread-3",
                run_id="run-3",
            )

            first.release()
            same_workspace.release()
            second.release()

            self.assertIsNone(
                registry.active_owner(workspace_root=first_workspace, project_id=None)
            )
            self.assertIsNone(
                registry.active_owner(workspace_root=second_workspace, project_id=None)
            )

    def test_same_workspace_stream_reaches_graph_without_busy_failure(self) -> None:
        """已有运行只保留登记，新流仍应进入 Graph。"""

        with tempfile.TemporaryDirectory() as workspace:
            held_lease = workspace_run_leases.acquire(
                workspace_root=workspace,
                project_id=None,
                thread_id="thread-owner",
                run_id="run-owner",
            )
            graph = RecordingGraph()

            async def collect() -> str:
                stream = build_workflow_ag_ui_stream(
                    graph=graph,
                    payload={
                        "threadId": "thread-conflict",
                        "runId": "run-conflict",
                        "messages": [{"role": "user", "content": "modify app"}],
                        "forwardedProps": {"workspaceRoot": workspace},
                    },
                    accept="text/event-stream",
                )
                return "\n".join([frame async for frame in stream])

            try:
                payload = asyncio.run(collect())
            finally:
                held_lease.release()

            self.assertEqual(graph.calls, 1)
            self.assertIn("thread-conflict", payload)
            self.assertIn("run-conflict", payload)
            self.assertNotIn("workspace_busy", payload)
            self.assertIn("workflow.run.finished", payload)

    def test_stream_releases_workspace_after_graph_error(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:

            async def collect() -> None:
                stream = build_workflow_ag_ui_stream(
                    graph=FailingGraph(),
                    payload={
                        "threadId": "thread-error",
                        "runId": "run-error",
                        "messages": [{"role": "user", "content": "modify app"}],
                        "forwardedProps": {"workspaceRoot": workspace},
                    },
                    accept="text/event-stream",
                )
                _ = [frame async for frame in stream]

            asyncio.run(collect())

            self.assertIsNone(
                workspace_run_leases.active_owner(
                    workspace_root=workspace,
                    project_id=None,
                )
            )

    def test_stream_releases_workspace_when_consumer_is_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:

            async def cancel_stream() -> None:
                graph = WaitingGraph()
                stream = build_workflow_ag_ui_stream(
                    graph=graph,
                    payload={
                        "threadId": "thread-cancel",
                        "runId": "run-cancel",
                        "messages": [{"role": "user", "content": "modify app"}],
                        "forwardedProps": {"workspaceRoot": workspace},
                    },
                    accept="text/event-stream",
                )

                async def consume() -> None:
                    _ = [frame async for frame in stream]

                task = asyncio.create_task(consume())
                await graph.started.wait()
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

            asyncio.run(cancel_stream())

            self.assertIsNone(
                workspace_run_leases.active_owner(
                    workspace_root=workspace,
                    project_id=None,
                )
            )


if __name__ == "__main__":
    unittest.main()
