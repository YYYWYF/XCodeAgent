"""在真实后台文件写入期间取消提交者，验证单写锁与已提交快照仍一致。"""

import asyncio
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.services.planning_run_controller import PlanningRunController, PlanningRunPersistenceError
from app.services.planning_run_events import GenerationStarted, RunCancelled
from app.workspace.planning_run_documents import load_planning_run, write_planning_run_atomic
from tests.planning_run_fixtures import AT, run


class BlockingWriter:
    """使用显式线程/协程信号控制首次真实写入，避免依赖随机 sleep 时序。"""

    def __init__(self, *, fail_first=False):
        """保存测试事件循环与写入门闩，便于从 IO 线程报告进入临界区。"""

        self.loop = asyncio.get_running_loop()
        self.entered = asyncio.Event()
        self.release = threading.Event()
        self.revisions = []
        self.fail_first = fail_first

    def __call__(self, workspace, snapshot):
        """首次写入被门闩阻塞，放行后调用 T5.3 真实原子 writer 或模拟失败。"""

        self.revisions.append(snapshot.revision)
        if len(self.revisions) == 1:
            self.loop.call_soon_threadsafe(self.entered.set)
            if not self.release.wait(timeout=3):
                raise TimeoutError("测试未释放写入门闩。")
            if self.fail_first:
                raise OSError("disk unavailable")
        return write_planning_run_atomic(workspace, snapshot)


class PlanningRunControllerCancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """每个测试使用独立临时文件及 Controller，避免共享事务状态。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = {"workspace": directory.name}
        self.controller = PlanningRunController(run(), self.workspace)

    async def test_cancel_during_io_drains_commit_before_unlock_and_next_event(self):
        """多次取消不能释放仍在写入的单写锁；后一事件最终读取前一事件提交结果。"""

        writer = BlockingWriter()
        self.addCleanup(writer.release.set)
        with patch("app.services.planning_run_controller.write_planning_run_atomic", side_effect=writer):
            first = asyncio.create_task(self.controller.apply(GenerationStarted(at=AT)))
            await asyncio.wait_for(writer.entered.wait(), 2)
            first.cancel()
            await asyncio.sleep(0)
            first.cancel()
            second = asyncio.create_task(self.controller.apply(RunCancelled(at=AT)))
            await asyncio.sleep(0)
            try:
                self.assertFalse(first.done())
                self.assertFalse(second.done())
                self.assertEqual(writer.revisions, [1])
                self.assertEqual(self.controller.snapshot.revision, 0)
                self.assertIsNone(load_planning_run(self.workspace))
            finally:
                writer.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(first, 2)
            cancelled = await asyncio.wait_for(second, 2)
        self.assertEqual(writer.revisions, [1, 2])
        self.assertEqual((cancelled.revision, cancelled.status), (2, "cancelled"))
        self.assertEqual(load_planning_run(self.workspace)["revision"], 2)

    async def test_cancelled_lock_waiter_never_starts_an_event(self):
        """尚未取得单写锁的提交者被取消后不执行转换、不写文件。"""

        writer = BlockingWriter()
        self.addCleanup(writer.release.set)
        with patch("app.services.planning_run_controller.write_planning_run_atomic", side_effect=writer):
            first = asyncio.create_task(self.controller.apply(GenerationStarted(at=AT)))
            await asyncio.wait_for(writer.entered.wait(), 2)
            waiter = asyncio.create_task(self.controller.apply(RunCancelled(at=AT)))
            await asyncio.sleep(0)
            waiter.cancel()
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(waiter, 2)
                self.assertEqual(writer.revisions, [1])
            finally:
                writer.release.set()
            await asyncio.wait_for(first, 2)
        self.assertEqual(self.controller.snapshot.status, "active")
        self.assertEqual(self.controller.snapshot.revision, 1)
        self.assertEqual(writer.revisions, [1])

    async def test_cancel_during_failed_io_surfaces_failure_and_preserves_revision(self):
        """提交者取消不能掩盖底层持久化错误；后续事件仍从最近已提交版本开始。"""

        writer = BlockingWriter(fail_first=True)
        self.addCleanup(writer.release.set)
        with patch("app.services.planning_run_controller.write_planning_run_atomic", side_effect=writer):
            first = asyncio.create_task(self.controller.apply(GenerationStarted(at=AT)))
            await asyncio.wait_for(writer.entered.wait(), 2)
            first.cancel()
            second = asyncio.create_task(self.controller.apply(RunCancelled(at=AT)))
            await asyncio.sleep(0)
            writer.release.set()
            with self.assertRaises(PlanningRunPersistenceError):
                await asyncio.wait_for(first, 2)
            cancelled = await asyncio.wait_for(second, 2)
        self.assertEqual(writer.revisions, [1, 1])
        self.assertEqual((cancelled.revision, cancelled.status), (1, "cancelled"))
        self.assertEqual(load_planning_run(self.workspace)["phase"], "preparing")
