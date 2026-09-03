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


if __name__ == "__main__":
    unittest.main()
