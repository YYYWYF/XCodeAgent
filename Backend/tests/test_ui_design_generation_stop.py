"""覆盖真实线程退出、旧代隔离、规划 End 及 AG-UI 停止回执。"""

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.ui_design_generation_pool import UiDesignGenerationPool
from app.services.ui_design_generation_task import run_ui_design_thread
from app.services.ui_design_generator import load_page_code, persist_page_code
from app.services.run_workers import bind_run_workers, current_run_id, active_worker_runs
from app.services.planning_execution_end import end_planning_execution
from app.services.ended_planning import ended_planning_available
from app.protocols.ui_design_generation import build_ui_design_generation_stream
from app.services.ui_design_generation_control import inspect_page
from tests.test_ui_design_generation_pool import _task, FAKE_CODE
from tests import test_planning_execution_end as end_fixture


class UiDesignGenerationStopTests(unittest.IsolatedAsyncioTestCase):
    """仅模型候选受控，池、文件、线程和生命周期使用生产实现。"""

    async def asyncSetUp(self):
        """每例使用隔离工作区、生成池及可控制的模型线程。"""
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = str(Path(self.tmp.name).resolve())
        self.project_dir = str(Path(self.workspace) / ".devagentstudio/ui-design")
        self.pool = UiDesignGenerationPool(1)
        self.started, self.release = threading.Event(), threading.Event()
        self.calls = []

    async def asyncTearDown(self):
        """先排空模型线程再关闭本例 worker，禁止测试取消掩盖线程泄露。"""
        self.release.set()
        await asyncio.wait_for(self.pool._queue.join(), 5)
        for worker in self.pool._workers:
            worker.cancel()
        await asyncio.gather(*self.pool._workers, return_exceptions=True)
        from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
        await close_workflow_checkpointer_for_workspace(workspace=self.workspace)
        self.tmp.cleanup()

    def generate(self, page, page_key, project_dir, **kwargs):
        """保留真实同步线程，取消回调由生产任务入口负责检查。"""
        self.calls.append(current_run_id())
        self.started.set()
        if not self.release.wait(5):
            raise RuntimeError("验证模型线程超时")
        return FAKE_CODE

    def page(self):
        """读取生产清单中的当前页。"""
        return self.pool._read_manifest(self.workspace, "proj")["pages"][0]

    async def test_timeout_preserves_owner_old_code_and_retry_requires_new_generation(self):
        """超时仍为 stopping，不覆盖旧稿；退出后新生成不受旧取消或旧队列项影响。"""
        persist_page_code(self.project_dir, "Orders", "旧稿")
        task = _task(self.workspace, self.project_dir)
        with patch("app.services.ui_design_generation_task.generate_page_react_code", side_effect=self.generate):
            await self.pool.submit([task])
            self.assertTrue(await asyncio.to_thread(self.started.wait, 2))
            old_id = self.page()["generation_id"]
            with self.assertRaises(TimeoutError):
                await self.pool.cancel_page(self.workspace, "orders", generation_id=old_id, timeout_seconds=.01)
            self.assertEqual(self.page()["status"], "stopping")
            self.assertTrue(self.pool.is_active(self.workspace, "orders"))
            self.assertEqual(await self.pool.submit([task]), [])
            self.assertEqual(load_page_code(self.project_dir, "Orders"), "旧稿")
            self.release.set()
            await self.pool._queue.join()
            self.assertEqual(self.page()["status"], "cancelled")
            self.assertEqual(load_page_code(self.project_dir, "Orders"), "旧稿")
            await self.pool.submit([task])
            self.assertNotEqual(self.page()["generation_id"], old_id)
            with self.assertRaisesRegex(ValueError, "已变化"):
                await self.pool.cancel_page(self.workspace, "orders", generation_id=old_id)
            await self.pool._queue.join()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.page()["status"], "confirmed")

    async def test_cancelled_queue_item_cannot_run_or_cleanup_new_generation(self):
        """在 worker 领取前取消并立即重提，旧队列项不能生成或清掉新 owner。"""
        self.release.set()
        task = _task(self.workspace, self.project_dir)
        with patch("app.services.ui_design_generation_task.generate_page_react_code", side_effect=self.generate):
            await self.pool.submit([task])
            await self.pool.cancel_page(self.workspace, "orders")
            await self.pool.submit([task])
            await self.pool._queue.join()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.page()["status"], "confirmed")

    async def test_each_task_has_independent_model_run(self):
        """常驻 worker 不继承第一个调用方 Run，每次真实模型请求有自己的取消身份。"""
        self.release.set()
        with patch("app.services.ui_design_generation_task.generate_page_react_code", side_effect=self.generate):
            for source in ("first-source", "second-source"):
                with bind_run_workers(source, self.workspace):
                    await self.pool.submit([_task(self.workspace, self.project_dir)])
                await self.pool._queue.join()
        self.assertEqual(len(set(self.calls)), 2)
        self.assertTrue(all(run.startswith("ui-design-") for run in self.calls))

    async def test_end_waits_for_ui_pool_and_saves_stable_fingerprint(self):
        """整个规划 End 必须等待 UI 池退出，结束后文件不再变化且仍可继续。"""
        await end_fixture.PlanningExecutionEndTests.prepare(self, self.workspace)
        with patch("app.services.ui_design_generation_pool._POOL", self.pool), patch(
                "app.services.ui_design_generation_task.generate_page_react_code", side_effect=self.generate):
            await self.pool.submit([_task(self.workspace, self.project_dir)])
            self.assertTrue(await asyncio.to_thread(self.started.wait, 2))
            ending = asyncio.create_task(end_planning_execution(self.workspace, "thread"))
            await asyncio.sleep(.01)
            self.assertFalse(ending.done())
            self.assertEqual(self.page()["status"], "stopping")
            with self.assertRaisesRegex(RuntimeError, "正在停止"):
                await self.pool.submit([_task(self.workspace, self.project_dir, page_id="dashboard")])
            self.release.set()
            ended = await ending
            await self.pool._queue.join()
        self.assertFalse(self.pool.pending_page_ids(self.workspace))
        self.assertEqual(self.page()["status"], "cancelled")
        self.assertTrue(ended_planning_available(self.workspace, ended))

    async def test_ag_ui_success_follows_worker_exit_and_failure_has_complete_lifecycle(self):
        """停止事件流先报告进度，退出后发成功；过时代次失败仍发完整生命周期。"""
        task = _task(self.workspace, self.project_dir)
        frames = []

        async def consume(payload):
            """消费生产 AG-UI 流并保留官方事件。"""
            async for frame in build_ui_design_generation_stream(payload=payload):
                frames.extend(json.loads(line[5:]) for line in frame.splitlines() if line.startswith("data:"))

        with patch("app.services.ui_design_generation_pool._POOL", self.pool), patch(
                "app.services.ui_design_generation_task.generate_page_react_code", side_effect=self.generate):
            await self.pool.submit([task])
            self.assertTrue(await asyncio.to_thread(self.started.wait, 2))
            request = {"forwardedProps": {"uiDesignGeneration": {"action": "cancel",
                "workspaceRoot": self.workspace, "pageId": "orders",
                "stopToken": (await inspect_page(self.pool, self.workspace, "orders"))["stopToken"]}}}
            consumer = asyncio.create_task(consume(request))
            await asyncio.sleep(.01)
            self.assertFalse(consumer.done())
            self.assertNotIn("RUN_FINISHED", [frame["type"] for frame in frames])
            self.release.set()
            await consumer
            self.assertEqual(frames[-1]["type"], "RUN_FINISHED")
            self.assertTrue(any(frame.get("value", {}).get("settled") for frame in frames))
            frames.clear()
            await consume(request)
            self.assertTrue(any(frame.get("value", {}).get("settled") for frame in frames))
            await self.pool.submit([task])
            await self.pool._queue.join()
            frames.clear()
            await consume(request)
            self.assertEqual(frames[0]["type"], "RUN_STARTED")
            self.assertEqual(frames[-1]["type"], "RUN_FINISHED")
            self.assertTrue(any(frame.get("value", {}).get("status") == "failed" for frame in frames))

    async def test_async_ui_node_cancellation_waits_for_its_sync_agent(self):
        """文本调整的异步节点取消也必须等待同步 Agent，而非只取消 to_thread 外壳。"""
        with bind_run_workers("ui-adjust-run", self.workspace):
            worker = asyncio.create_task(run_ui_design_thread(self.generate, {}, "Orders", self.project_dir))
        self.assertTrue(await asyncio.to_thread(self.started.wait, 2))
        worker.cancel()
        await asyncio.sleep(.01)
        self.assertFalse(worker.done())
        self.assertIn("ui-adjust-run", active_worker_runs(self.workspace))
        self.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await worker
        self.assertNotIn("ui-adjust-run", active_worker_runs(self.workspace))

    async def test_end_archives_latest_ui_checkpoint_instead_of_initial_owner(self):
        """多轮 UI 动作结束必须封存最后的 Run，防止只取消最初 owner 后继续按钮空转。"""
        from app.graph.application_planning_workflow import application_planning_graph_for_request
        from app.persistence.execution_recovery import get_execution, insert_execution

        await end_fixture.PlanningExecutionEndTests.prepare(self, self.workspace)
        original = await get_execution(self.workspace, "design-run")
        await insert_execution(original.model_copy(update={"run_id": "ui-action-run"}))
        graph = await application_planning_graph_for_request(workspace=self.workspace)
        await graph.aupdate_state({"configurable": {"thread_id": "thread"}}, {
            "workspace": self.workspace, "active_run_id": "ui-action-run", "active_thread_id": "thread",
            "request": "生成设计稿", "workflow_scope": "application_planning",
            "phase": "ui_confirmation", "status": "requires_user_input",
        }, as_node="ui_confirmation")
        with patch("app.services.ui_design_generation_pool._POOL", self.pool):
            ended = await end_planning_execution(self.workspace, "thread")
        self.assertEqual(ended.ended_planning_execution.run_id, "ui-action-run")
        self.assertIsNone(ended.active_run_id)
        self.assertEqual((await get_execution(self.workspace, "ui-action-run")).status, "cancelled")
        self.assertEqual((await get_execution(self.workspace, "design-run")).status, "cancelled")
        self.assertTrue(ended_planning_available(self.workspace, ended))
