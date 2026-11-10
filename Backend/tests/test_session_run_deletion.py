"""会话删除仅停止其自身登记的运行。"""

import asyncio
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from app.protocols.workflow.run_control import WorkflowRunRegistry
from app.protocols.application_lifecycle import ApplicationLifecycleAction, _prepare_session_deletion
from app.services.application_lifecycle import load_application_lifecycle, start_workbench_execution
from app.services.run_workers import bind_run_workers, run_worker
from app.services.workspace_process_registry import workspace_process_registry
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class SessionRunDeletionTests(unittest.IsolatedAsyncioTestCase):
    """验证同工作区运行按真实 Graph thread 隔离。"""

    async def test_deletion_waits_for_worker_after_outer_task_unregisters(self) -> None:
        """外层协程已注销时，仍按 owner Run 等待真实同步工作退出。"""
        with tempfile.TemporaryDirectory() as workspace:
            registry = WorkflowRunRegistry()
            started, release = threading.Event(), threading.Event()
            run_id = "deleted-session-worker"
            workspace_process_registry.allow_run(run_id)

            def work():
                """模拟取消后尚未完成的同步模型或写盘步骤。"""
                with bind_run_workers(run_id, workspace), run_worker():
                    started.set()
                    release.wait(5)

            worker = asyncio.create_task(asyncio.to_thread(work))
            await asyncio.to_thread(started.wait, 2)
            try:
                result = await registry.cancel_thread(
                    workspace, "deleted-thread", run_ids=(run_id,), timeout_seconds=0.01,
                )
                self.assertEqual(result["remainingRunIds"], [run_id])
                self.assertTrue(registry.is_active(run_id, workspace=workspace))
                waiting = asyncio.create_task(registry.cancel_thread(
                    workspace, "deleted-thread", run_ids=(run_id,), timeout_seconds=2,
                ))
                await asyncio.sleep(0.02)
                self.assertFalse(waiting.done())
                release.set()
                self.assertFalse((await waiting)["remainingRunIds"])
            finally:
                release.set()
                await worker

    async def test_deletion_timeout_preserves_execution_locks_and_checkpoints(self) -> None:
        """未确认真实退出时拒绝删除，保留 owner 的执行、资源锁及 checkpoint。"""
        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            before = start_workbench_execution(
                workspace, scope="page", target_id="orders", page_id="orders",
                thread_id="owned-graph", owner_session_id="owner", run_id="owned-run", phase="build",
            )
            request = ApplicationLifecycleAction(
                action="prepare_session_deletion", workspaceRoot=workspace,
                sessionId="owner", sessionThreadId="visible-thread",
            )
            with patch(
                "app.protocols.workflow.run_control.workflow_run_registry.cancel_and_wait",
                new=AsyncMock(return_value="cancel_timeout"),
            ) as cancel, patch(
                "app.protocols.application_lifecycle.delete_workflow_checkpoints_for_threads",
                new=AsyncMock(),
            ) as delete:
                with self.assertRaisesRegex(RuntimeError, "仍未退出"):
                    await _prepare_session_deletion(request)
                cancel.assert_awaited_once_with("owned-run", workspace=workspace, ending=True, timeout_seconds=30.0)
                delete.assert_not_awaited()
            after = load_application_lifecycle(workspace)
            self.assertEqual(after.active_executions, before.active_executions)
            self.assertEqual(after.resource_locks, before.resource_locks)

    async def test_cancel_thread_preserves_other_session(self) -> None:
        """目标任务退出后，另一会话的任务仍可继续运行。"""

        registry = WorkflowRunRegistry()
        target = asyncio.create_task(asyncio.sleep(60))
        other = asyncio.create_task(asyncio.sleep(60))
        registry.register("target-run", target, workspace="/tmp/session-test", thread_id="target")
        registry.register("other-run", other, workspace="/tmp/session-test", thread_id="other")
        try:
            registry.begin_thread_deletion("/tmp/session-test", "target")
            result = await registry.cancel_thread("/tmp/session-test", "target")
            self.assertEqual(result["requestedRunIds"], ["target-run"])
            self.assertFalse(result["remainingRunIds"])
            self.assertTrue(target.cancelled())
            self.assertFalse(other.done())
            with self.assertRaises(RuntimeError):
                registry.register(
                    "new-target", asyncio.current_task(),
                    workspace="/tmp/session-test", thread_id="target",
                )
        finally:
            registry.end_thread_deletion("/tmp/session-test", "target")
            other.cancel()
            await asyncio.gather(target, other, return_exceptions=True)
            registry.unregister("target-run", target)
            registry.unregister("other-run", other)


if __name__ == "__main__":
    unittest.main()
