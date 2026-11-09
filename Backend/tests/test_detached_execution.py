"""真实后台生成器、持久接受及标准 AG-UI 输出的断连回归。"""

import asyncio
import json
import tempfile
import unittest
from ag_ui.core import RunStartedEvent, RunFinishedEvent, CustomEvent, TextMessageStartEvent, TextMessageContentEvent
from app.protocols.execution_event_encoder import EventEncoder
from app.services.detached_execution import build_detached_execution_stream, shutdown_detached_executions
from app.persistence.execution_streams import latest_execution_output


class DetachedExecutionTests(unittest.IsolatedAsyncioTestCase):
    """关闭订阅不取消业务；重复提交及重启后的结果读取都不能再次执行。"""

    async def asyncSetUp(self):
        """为每次提交使用独立工作区及可控业务门。"""
        self.directory = tempfile.TemporaryDirectory()
        self.workspace = self.directory.name
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.done = asyncio.Event()
        self.calls = 0
        self.payload = {"runId": "request-a", "threadId": "thread-a", "messages": [{"role": "user", "content": "生成后等确认"}],
            "forwardedProps": {"workspaceRoot": self.workspace, "sessionId": "session-a"}}

    async def asyncTearDown(self):
        """先停止测试持有的后台任务，再释放数据库目录。"""
        await shutdown_detached_executions()
        self.directory.cleanup()

    async def source(self, payload):
        """模拟只运行一次并在产物确认门结束的真实业务生成器。"""
        self.calls += 1
        encoder = EventEncoder()
        yield encoder.encode(RunStartedEvent(threadId=payload["threadId"], runId=payload["runId"]))
        self.started.set()
        yield encoder.encode(TextMessageStartEvent(messageId="answer", role="assistant"))
        await self.release.wait()
        yield encoder.encode(TextMessageContentEvent(messageId="answer", delta="已生成，等待确认"))
        workflow = {"runId": payload["runId"], "threadId": payload["threadId"], "summary": {"status": "requires_user_input"}, "state": {}, "events": []}
        yield encoder.encode(CustomEvent(name="workflow-run", value=workflow))
        yield encoder.encode(RunFinishedEvent(threadId=payload["threadId"], runId=payload["runId"], result={"workflow": workflow}))
        self.done.set()

    def stream(self, payload=None):
        """复用生产后台入口，不能用模拟连接代替真实生成器生命周期。"""
        return build_detached_execution_stream(payload=payload or self.payload, endpoint="/workflow/run", source=self.source)

    async def test_disconnect_completes_original_and_replays_without_execution(self):
        """连接关闭后原任务完成，重复提交只返回同一份已保存确认结果。"""
        first = self.stream()
        await anext(first)
        await first.aclose()
        await self.started.wait()
        self.release.set()
        await asyncio.wait_for(self.done.wait(), 1)
        frames = [frame async for frame in self.stream()]
        self.assertEqual(self.calls, 1)
        self.assertIn("已生成，等待确认", "".join(frames))
        output = await latest_execution_output(self.workspace, "thread-a", "session-a")
        self.assertIs(output["finished"], True)
        self.assertEqual(output["content"], "已生成，等待确认")
        self.assertNotIn("workflow", output)
        self.assertIn('"status":"requires_user_input"', "".join(frames))

    async def test_concurrent_duplicate_subscribes_but_changed_request_is_rejected(self):
        """两个连接共享原执行；复用身份修改正文不能触发第二次业务。"""
        first = self.stream()
        await anext(first)
        duplicate = self.stream()
        await anext(duplicate)
        changed = {**self.payload, "messages": [{"role": "user", "content": "不同操作"}]}
        frames = [frame async for frame in self.stream(changed)]
        self.assertIn("EXECUTION_REQUEST_CONFLICT", "".join(frames))
        self.assertEqual(self.calls, 1)
        self.release.set()
        await first.aclose()
        await duplicate.aclose()
        await asyncio.wait_for(self.done.wait(), 1)

    async def test_new_request_cannot_bypass_running_thread(self):
        """丢失前端运行内存后另造请求身份，也不能并发执行同一线程。"""
        first = self.stream()
        await anext(first)
        frames = [frame async for frame in self.stream({**self.payload, "runId": "request-b"})]
        self.assertIn("EXECUTION_THREAD_BUSY", "".join(frames))
        self.assertEqual(self.calls, 1)
        self.release.set()
        await first.aclose()

    async def test_output_is_session_scoped(self):
        """其他会话不能取回本任务输出。"""
        first = self.stream()
        await anext(first)
        output = await latest_execution_output(self.workspace, "thread-a", "session-a")
        self.assertIs(output["finished"], False)
        self.assertIsNone(await latest_execution_output(self.workspace, "thread-a", "other-session"))
        self.release.set()
        await first.aclose()

    async def test_backend_loss_cannot_republish_old_running_output(self):
        """Backend 更换后不继续追踪旧展示，也不以旧运行快照覆盖当前恢复入口。"""
        from unittest.mock import patch
        from types import SimpleNamespace
        first = self.stream()
        await anext(first)
        with patch("app.services.backend_instance.current_backend_instance", return_value=SimpleNamespace(instance_id="new-backend")):
            output = await latest_execution_output(self.workspace, "thread-a", "session-a")
        self.assertTrue(output["finished"])
        self.assertNotIn("workflow", output)
        self.release.set()
        await first.aclose()

    async def test_asgi_disconnect_does_not_cancel_generator(self):
        """真实 StreamingResponse 收到 http.disconnect 后，后台仍独立执行到确认门。"""
        from starlette.responses import StreamingResponse
        delivered = asyncio.Event()
        async def send(message):
            """第一份正文交付后模拟 Renderer 进程退出。"""
            if message["type"] == "http.response.body" and message.get("body"):
                delivered.set()
        async def receive():
            """通过 ASGI 标准断连事件取消响应订阅。"""
            await delivered.wait()
            return {"type": "http.disconnect"}
        await StreamingResponse(self.stream())({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
        self.release.set()
        await asyncio.wait_for(self.done.wait(), 1)
        frames = [frame async for frame in self.stream()]
        self.assertIn("已生成，等待确认", "".join(frames))
        self.assertEqual(self.calls, 1)

    async def test_explicit_stop_cancels_accepted_task_before_first_checkpoint(self):
        """没有原 Runtime 登记时，原停止入口仍能取消已接受的后台任务。"""
        from app.protocols.workflow.run_control import workflow_run_registry
        entered = asyncio.Event()
        async def pending_source(payload):
            """模拟 Graph 尚未建立首个现场的准备阶段。"""
            entered.set()
            await self.release.wait()
            yield EventEncoder().encode(RunFinishedEvent(threadId=payload["threadId"], runId=payload["runId"]))
        async def consume():
            """持有真实请求订阅，等待控制端停止后台任务。"""
            return [frame async for frame in build_detached_execution_stream(payload=self.payload, endpoint="/workflow/run", source=pending_source)]
        subscriber = asyncio.create_task(consume())
        await entered.wait()
        status = await workflow_run_registry.cancel_and_wait("request-a", workspace=self.workspace, timeout_seconds=1)
        self.assertEqual(status, "cancelled")
        frames = await asyncio.wait_for(subscriber, 1)
        self.assertIn("EXECUTION_CANCELLED", "".join(frames))
