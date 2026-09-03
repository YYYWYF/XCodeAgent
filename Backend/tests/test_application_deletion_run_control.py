from __future__ import annotations

import asyncio
from tempfile import TemporaryDirectory
import unittest

from app.protocols.workflow.run_control import WorkflowRunRegistry


class ApplicationDeletionRunControlTests(unittest.IsolatedAsyncioTestCase):
    """验证应用删除栅栏只取消目标工作区且拒绝新的运行。"""

    async def test_workspace_deletion_cancels_only_matching_tasks(self) -> None:
        """同名或其他路径工作区的任务不能被目标应用删除误伤。"""

        registry = WorkflowRunRegistry()
        with TemporaryDirectory() as directory:
            first_workspace = f"{directory}/first"
            second_workspace = f"{directory}/second"
            first = asyncio.create_task(asyncio.Event().wait())
            second = asyncio.create_task(asyncio.Event().wait())
            registry.register("run-first", first, workspace=first_workspace)
            registry.register("run-second", second, workspace=second_workspace)

            registry.begin_workspace_deletion(first_workspace)
            result = await registry.cancel_workspace(first_workspace)

            self.assertEqual(result["requestedRunIds"], ["run-first"])
            self.assertTrue(first.cancelled())
            self.assertFalse(second.done())
            with self.assertRaisesRegex(RuntimeError, "正在删除"):
                registry.register(
                    "run-blocked",
                    asyncio.current_task(),  # type: ignore[arg-type]
                    workspace=first_workspace,
                )

            second.cancel()
            await asyncio.gather(second, return_exceptions=True)

    async def test_workspace_cancellation_returns_when_task_ignores_cancel(self) -> None:
        """不响应首次取消的任务必须在超时后作为 remainingRunIds 返回。"""

        registry = WorkflowRunRegistry()
        started = asyncio.Event()
        release = asyncio.Event()

        async def ignore_first_cancellation() -> None:
            """捕获取消并等待测试主动释放，模拟无法及时退出的工作流。"""

            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()

        with TemporaryDirectory() as directory:
            workspace = f"{directory}/stubborn"
            task = asyncio.create_task(ignore_first_cancellation())
            registry.register("run-stubborn", task, workspace=workspace)
            await started.wait()
            try:
                result = await asyncio.wait_for(
                    registry.cancel_workspace(workspace, timeout_seconds=0.01),
                    timeout=0.5,
                )

                self.assertEqual(result["requestedRunIds"], ["run-stubborn"])
                self.assertEqual(result["remainingRunIds"], ["run-stubborn"])
                self.assertFalse(task.done())
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
