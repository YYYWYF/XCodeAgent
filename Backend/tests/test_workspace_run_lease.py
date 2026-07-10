from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path

from app.protocols.workflow_visualization import build_workflow_ag_ui_stream
from app.workspace.run_lease import (
    WORKSPACE_BUSY_ERROR_CODE,
    WorkspaceBusyError,
    WorkspaceRunLeaseRegistry,
    workspace_run_leases,
)


class NeverCalledGraph:
    def __init__(self) -> None:
        self.calls = 0

    async def astream(self, initial_state, *, config, stream_mode):
        self.calls += 1
        if False:
            yield {}


class FailingGraph:
    async def astream(self, initial_state, *, config, stream_mode):
        raise RuntimeError("graph failed")
        if False:
            yield {}


class WaitingGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def astream(self, initial_state, *, config, stream_mode):
        self.started.set()
        await self.release.wait()
        if False:
            yield {}


class WorkspaceRunLeaseTests(unittest.TestCase):
    def test_same_workspace_is_rejected_and_different_workspace_is_allowed(self) -> None:
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
            with self.assertRaises(WorkspaceBusyError) as caught:
                registry.acquire(
                    workspace_root=str(Path(first_workspace) / "."),
                    project_id=None,
                    thread_id="thread-2",
                    run_id="run-2",
                )
            second = registry.acquire(
                workspace_root=second_workspace,
                project_id=None,
                thread_id="thread-2",
                run_id="run-2",
            )

            self.assertEqual(caught.exception.code, WORKSPACE_BUSY_ERROR_CODE)
            self.assertEqual(caught.exception.owner.run_id, "run-1")
            first.release()
            second.release()

            self.assertIsNone(
                registry.active_owner(workspace_root=first_workspace, project_id=None)
            )
            self.assertIsNone(
                registry.active_owner(workspace_root=second_workspace, project_id=None)
            )

    def test_conflicting_stream_returns_ag_ui_failure_without_calling_graph(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            held_lease = workspace_run_leases.acquire(
                workspace_root=workspace,
                project_id=None,
                thread_id="thread-owner",
                run_id="run-owner",
            )
            graph = NeverCalledGraph()

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

            self.assertEqual(graph.calls, 0)
            self.assertIn("thread-conflict", payload)
            self.assertIn("run-conflict", payload)
            self.assertIn(WORKSPACE_BUSY_ERROR_CODE, payload)
            self.assertIn("workflow.run.failed", payload)

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
