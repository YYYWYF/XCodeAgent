"""验证公共 AG-UI 进度流退出后不残留业务或队列等待任务。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest

from starlette.responses import StreamingResponse

from app.protocols.ag_ui_action_stream import (
    AgUiActionProgress,
    AgUiActionResult,
    build_ag_ui_action_stream,
)
from app.protocols.workflow.run_control import workflow_run_registry


class AgUiActionStreamCancellationTests(unittest.IsolatedAsyncioTestCase):
    """使用真实流和 ASGI 断连验证取消收口，不调用模型或真实应用。"""

    async def asyncSetUp(self) -> None:
        """记录测试前已有任务，避免把运行器任务误认为动作残留。"""
        self.initial_tasks = asyncio.all_tasks()

    async def asyncTearDown(self) -> None:
        """即使回归断言失败也回收孤立任务，避免影响后续用例。"""
        tasks = self.remaining_tasks()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def remaining_tasks(self) -> set[asyncio.Task]:
        """只返回本用例创建且尚未退出的任务。"""
        return asyncio.all_tasks() - self.initial_tasks - {asyncio.current_task()}

    async def test_cancelling_idle_progress_or_text_stream_drains_all_children(self) -> None:
        """取消等待首个进度或文本的流必须同时结束业务和消息等待任务。"""
        for streaming in (False, True):
            with self.subTest(streaming=streaming), tempfile.TemporaryDirectory() as workspace:
                started = asyncio.Event()
                stopped = asyncio.Event()
                release = asyncio.Event()

                async def operation(*_reporters) -> AgUiActionResult:
                    """模拟仍在执行且尚无消息的业务操作。"""
                    started.set()
                    try:
                        await release.wait()
                        return AgUiActionResult(data={}, message="完成。")
                    finally:
                        stopped.set()

                stream = build_ag_ui_action_stream(
                    payload={"threadId": "cancel-thread", "runId": "cancel-run"},
                    event_name="example", state_key="example", run_id_prefix="example",
                    error_message_prefix="失败", workspace_root=workspace,
                    **({"streaming_operation": operation} if streaming else {"progress_operation": operation}),
                )

                async def consume() -> None:
                    """持续消费真实生命周期，使取消发生在等待业务进度时。"""
                    async for _frame in stream:
                        pass

                consumer = asyncio.create_task(consume())
                await asyncio.wait_for(started.wait(), timeout=2)
                self.assertEqual(workflow_run_registry.workspace_active_runs(workspace), ["cancel-run"])
                consumer.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await consumer
                self.assertTrue(stopped.is_set())
                self.assertEqual(workflow_run_registry.workspace_active_runs(workspace), [])
                self.assertEqual(self.remaining_tasks(), set())

    async def test_asgi_disconnect_drains_progress_waiter(self) -> None:
        """真实 StreamingResponse 客户端断连也必须回收消息等待子任务。"""
        started = asyncio.Event()
        stopped = asyncio.Event()
        release = asyncio.Event()

        async def operation(_report) -> AgUiActionResult:
            """保持业务待执行，直到 ASGI 的断连取消传入。"""
            started.set()
            try:
                await release.wait()
                return AgUiActionResult(data={}, message="完成。")
            finally:
                stopped.set()

        async def receive() -> dict:
            """业务开始后模拟客户端断开，不使用定时等待制造竞态。"""
            await started.wait()
            return {"type": "http.disconnect"}

        async def send(_message: dict) -> None:
            """接收真实 ASGI 帧，模拟仍能消费响应头的客户端。"""

        stream = build_ag_ui_action_stream(
            payload={}, event_name="example", state_key="example", run_id_prefix="example",
            progress_operation=operation, error_message_prefix="失败",
        )
        response = StreamingResponse(stream, media_type="text/event-stream")
        await asyncio.wait_for(response(
            {"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send,
        ), timeout=2)
        self.assertTrue(stopped.is_set())
        self.assertEqual(self.remaining_tasks(), set())

    async def test_closing_unacknowledged_progress_cancels_business(self) -> None:
        """消费者在阶段帧后关流时，等待交付回执的业务不得继续执行。"""
        entered = False
        stopped = asyncio.Event()

        async def operation(report) -> AgUiActionResult:
            """等待首个阶段交付，验证关闭时不会越过该业务边界。"""
            nonlocal entered
            try:
                await report(AgUiActionProgress("generate", "正在生成。", 10))
                entered = True
                return AgUiActionResult(data={}, message="完成。")
            finally:
                stopped.set()

        stream = build_ag_ui_action_stream(
            payload={}, event_name="example", state_key="example", run_id_prefix="example",
            progress_operation=operation, error_message_prefix="失败",
        )
        for _ in range(3):
            await anext(stream)
        await stream.aclose()
        self.assertFalse(entered)
        self.assertTrue(stopped.is_set())
        self.assertEqual(self.remaining_tasks(), set())

    async def test_completed_or_failed_operation_finishes_without_waiters(self) -> None:
        """没有消息的业务成功或失败都应结束生命周期且不残留等待任务。"""
        for failed in (False, True):
            with self.subTest(failed=failed):
                async def operation(_report) -> AgUiActionResult:
                    """让出执行权后直接结束，覆盖业务先于队列就绪的分支。"""
                    await asyncio.sleep(0)
                    if failed:
                        raise ValueError("测试失败")
                    return AgUiActionResult(data={}, message="完成。")

                stream = build_ag_ui_action_stream(
                    payload={}, event_name="example", state_key="example", run_id_prefix="example",
                    progress_operation=operation, error_message_prefix="失败",
                )
                frames = [frame async for frame in stream]
                last = json.loads(next(line[6:] for line in frames[-1].splitlines() if line.startswith("data: ")))
                self.assertEqual(last["type"], "RUN_FINISHED")
                self.assertEqual(last["result"]["example"]["status"], "failed" if failed else "completed")
                self.assertEqual(self.remaining_tasks(), set())
