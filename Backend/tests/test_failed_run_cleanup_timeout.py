"""验证 AG-UI 失败收口不被真实同步线程的慢清理阻塞。"""

import asyncio
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.application_lifecycle import load_application_lifecycle
from app.services.run_workers import bind_run_workers, run_worker
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class FailedRunCleanupTimeoutTests(unittest.IsolatedAsyncioTestCase):
    """真实 Runtime 保留失败身份、及时结束流，后台退出后自动释放占用。"""

    async def test_graph_close_error_does_not_replace_original_failure(self):
        """关闭流的次生异常只记录诊断，不阻止已经退出的执行完成清理。"""
        from app.services.failed_run_cleanup import wait_failed_run

        async def close():
            """模拟 Graph 关闭时抛出次生错误。"""
            raise RuntimeError("secondary close failure")

        with self.assertLogs("app.services.failed_run_cleanup", level="WARNING"):
            self.assertTrue(await wait_failed_run("close-error", close()))

    async def test_runtime_publishes_failure_before_worker_exits(self):
        """慢回滚不能吞掉 RUN_ERROR；后台自动释放同 Run 的资源。"""
        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            ready, release = threading.Event(), threading.Event()

            def worker():
                """保留真实线程登记，直到测试允许回滚结束。"""
                with bind_run_workers("slow-runtime", workspace), run_worker():
                    ready.set()
                    release.wait(5)

            thread = threading.Thread(target=worker)

            class FailedGraph:
                """执行过程中留下一个慢回滚线程，再抛出原始错误。"""

                async def astream(self, state, **kwargs):
                    """模拟真实节点执行中失败，不伪造已完成结果。"""
                    thread.start()
                    await asyncio.to_thread(ready.wait, 1)
                    raise RuntimeError("original failure during execution")
                    yield

            try:
                with patch("app.services.failed_run_cleanup.FOREGROUND_CLEANUP_SECONDS", 0.02):
                    frames = "".join([frame async for frame in build_workflow_ag_ui_stream(
                        graph=FailedGraph(), payload={
                            "threadId": "slow-thread", "runId": "slow-runtime",
                            "messages": [{"role": "user", "content": "保留原始输入"}],
                            "forwardedProps": {"workspaceRoot": workspace},
                        },
                    )])
                self.assertTrue(thread.is_alive())
                self.assertIn("RUN_ERROR", frames)
                self.assertIn("original failure during execution", frames)
                self.assertIn("后台工作仍在退出", frames)
                state = load_application_lifecycle(workspace)
                self.assertEqual(state.active_executions["slow-runtime"].status.value, "failed")
                self.assertIsNotNone(state.resource_locks.application)
            finally:
                release.set()
                if thread.ident is not None:
                    await asyncio.to_thread(thread.join, 1)
                for _ in range(100):
                    if load_application_lifecycle(workspace).resource_locks.application is None:
                        break
                    await asyncio.sleep(.02)
            self.assertIsNone(load_application_lifecycle(workspace).resource_locks.application)
