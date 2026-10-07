"""会话删除仅停止其自身登记的运行。"""

import asyncio
import unittest

from app.protocols.workflow.run_control import WorkflowRunRegistry


class SessionRunDeletionTests(unittest.IsolatedAsyncioTestCase):
    """验证同工作区运行按真实 Graph thread 隔离。"""

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
