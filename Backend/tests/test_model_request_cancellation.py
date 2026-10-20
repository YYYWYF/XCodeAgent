"""通过真实 OpenAI SDK 和原始 HTTP 流验证暂停，不依赖模型交付可见文本。"""

import asyncio
import threading
import unittest

import httpx
from langchain_openai import ChatOpenAI

from app.protocols.workflow.run_control import WorkflowRunRegistry
from app.services.model_request_cancellation import (
    check_model_request, guard_model_response,
    check_async_model_request, guard_async_model_response,
)
from app.services.run_workers import bind_run_workers, run_worker, active_worker_runs
from app.services.workspace_process_registry import workspace_process_registry


class ModelRequestCancellationTests(unittest.IsolatedAsyncioTestCase):
    """暂停可以在 SSE 心跳边界退出，不必等待模型给出最终回答。"""

    async def test_sync_sdk_heartbeat_releases_real_worker(self):
        """SDK 过滤心跳之前中止响应，真实同步模型线程和连接均收尾。"""
        started, release, closed = threading.Event(), threading.Event(), threading.Event()
        run_id = "sync-model-heartbeat-pause"
        errors = []

        class Stream(httpx.SyncByteStream):
            """只发 SSE 心跳，永远不交付模型文本或完成标记。"""

            def __iter__(self):
                """让暂停发生在两个原始响应块之间。"""
                yield b": keep-alive\n\n"
                started.set()
                release.wait(3)
                yield b": keep-alive\n\n"
                raise AssertionError("取消后不应再读取下一块")

            def close(self):
                """记录真实响应关闭。"""
                closed.set()

        def respond(request):
            """返回供真实 SDK 消费的流式响应。"""
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())

        with httpx.Client(transport=httpx.MockTransport(respond), event_hooks={
            "request": [check_model_request], "response": [guard_model_response],
        }) as client:
            model = ChatOpenAI(model="probe", api_key="test", base_url="https://model.invalid/v1",
                               streaming=True, max_retries=0, http_client=client)

            def work():
                """保持真实 worker 登记直到 SDK 关闭响应。"""
                with run_worker():
                    try:
                        model.invoke("检查")
                    except BaseException as error:
                        errors.append(error)

            async def outer():
                """把真实同步模型调用放在线程中运行。"""
                with bind_run_workers(run_id, "/tmp/model-heartbeat-pause"):
                    await asyncio.to_thread(work)

            registry = WorkflowRunRegistry()
            task = asyncio.create_task(outer())
            registry.register(run_id, task, workspace="/tmp/model-heartbeat-pause")
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                cancellation = asyncio.create_task(registry.cancel_and_wait(run_id, timeout_seconds=3, ending=True))
                await asyncio.sleep(0.02)
                release.set()
                self.assertEqual(await cancellation, "cancelled")
                self.assertTrue(closed.is_set())
                self.assertIsInstance(errors[0], RuntimeError)
                self.assertNotIn(run_id, active_worker_runs())
            finally:
                release.set()

    async def test_async_heartbeat_stops_and_shared_client_remains_usable(self):
        """一个 Run 取消只关闭自己的响应，同一客户端仍能服务下次运行。"""
        run_id = "async-model-heartbeat-pause"
        closed = []

        class Stream(httpx.AsyncByteStream):
            """在两块异步响应之间发出取消。"""

            async def __aiter__(self):
                """没有业务文本也必须观察取消。"""
                yield b": ping\n\n"
                workspace_process_registry.cancel_run(run_id)
                yield b": ping\n\n"

            async def aclose(self):
                """记录异步连接收尾。"""
                closed.append(True)

        def respond(request):
            """以同一客户端分发本轮流和后续独立请求。"""
            if request.url.path == "/next":
                return httpx.Response(200, content=b"ok")
            return httpx.Response(200, stream=Stream())

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), event_hooks={
            "request": [check_async_model_request], "response": [guard_async_model_response],
        }) as client:
            with bind_run_workers(run_id, "/tmp/model-heartbeat-pause"):
                with self.assertRaisesRegex(RuntimeError, "已停止"):
                    async with client.stream("GET", "https://model.invalid/stream") as response:
                        async for _ in response.aiter_bytes():
                            pass
            self.assertTrue(closed)
            response = await client.get("https://model.invalid/next")
            self.assertEqual(response.content, b"ok")

    async def test_cancelled_request_never_reaches_transport(self):
        """SDK 重试或迟到请求在 HTTP 请求前就被截断。"""
        run_id = "cancelled-before-http"
        requests = []

        def respond(request):
            """记录不应发生的真实发送。"""
            requests.append(request)
            return httpx.Response(200)

        workspace_process_registry.cancel_run(run_id)
        with httpx.Client(transport=httpx.MockTransport(respond), event_hooks={"request": [check_model_request]}) as client:
            with bind_run_workers(run_id, "/tmp/model-heartbeat-pause"):
                with self.assertRaisesRegex(RuntimeError, "已停止"):
                    client.get("https://model.invalid")
        self.assertEqual(requests, [])
