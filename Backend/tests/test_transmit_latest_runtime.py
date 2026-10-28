"""验证转发与最新暂停、业务错误分类和原有网络重试的适配。"""

import json
import unittest
from unittest.mock import patch

import httpx

from app.agents.transmit_chat_model import TransmitChatModel
from app.config import Settings
from app.services.model_transmit_client import ModelTransmitClient, ModelTransmitError
from app.services.run_workers import bind_run_workers
from app.services.workspace_process_registry import workspace_process_registry
from app.services.model_transport_retry import run_with_transport_retry
from app.services.unit_model_failure import classify_unit_model_failure, MODEL_REQUEST_TIMEOUT, MODEL_CONNECTION_FAILED, MODEL_HTTP_ERROR
from tests.test_model_transmit import FakeRelay


class LatestRuntimeTests(unittest.IsolatedAsyncioTestCase):
    """传输变化不破坏现有取消边界和受限错误恢复语义。"""

    def test_frontend_run_is_retained_without_breaking_model_request_uniqueness(self):
        """同一业务 Run 两次模型请求拥有不同防重身份，并保留原始关联。"""
        relay = FakeRelay()
        model = TransmitChatModel(transmit_url="http://relay.test", transport=httpx.MockTransport(relay.handle))
        config = {"configurable": {"thread_id": "frontend-thread"},
                  "metadata": {"run_id": "frontend-round", "workflow_scope": "conversation", "project_id": "app1"}}
        model.invoke("one", config)
        model.invoke("two", config)
        calls = [payload for request, payload in relay.requests if request.url.path == "/model-inference/run"]
        self.assertNotEqual(calls[0]["runId"], calls[1]["runId"])
        for call in calls:
            self.assertEqual(call["threadId"], "frontend-thread")
            metadata = call["forwardedProps"]["modelInference"]["metadata"]
            self.assertEqual(metadata["extensions"]["studio_run_id"], "frontend-round")
            self.assertEqual(metadata["projectId"], "app1")

    async def test_cancelled_async_sse_heartbeat_closes_before_visible_content(self):
        """即使没有模型文本，取消也在原始 SSE 心跳边界生效并释放连接。"""
        run_id, closed = "transmit-heartbeat-cancel", []

        class Stream(httpx.AsyncByteStream):
            """只发心跳，在第二个字节块之前标记当前 Run 停止。"""

            async def __aiter__(self):
                """不依赖模型生成可见增量来响应暂停。"""
                yield b": heartbeat\n\n"
                workspace_process_registry.cancel_run(run_id)
                yield b": heartbeat\n\n"
                raise AssertionError("取消后不应继续读取")

            async def aclose(self):
                """记录本次响应已经关闭。"""
                closed.append(True)

        def handle(request):
            """仅返回模拟转发流，不访问网络。"""
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())

        client = ModelTransmitClient("http://relay.test", transport=httpx.MockTransport(handle))
        payload = client.payload("t1", "request-1", {})
        with bind_run_workers(run_id, "local-test-workspace"):
            with self.assertRaisesRegex(RuntimeError, "已停止"):
                async for _ in client.aiterate("/model-inference/run", payload):
                    pass
        self.assertTrue(closed)

    def test_cancelled_run_does_not_send_new_model_info_request(self):
        """暂停后的迟到请求在 HTTP 发送之前被阻止。"""
        requests = []
        run_id = "transmit-cancel-before-http"
        workspace_process_registry.cancel_run(run_id)

        def handle(request):
            """取消后不应该访问模拟传输。"""
            requests.append(request)
            return httpx.Response(200)

        client = ModelTransmitClient("http://relay.test", transport=httpx.MockTransport(handle))
        with bind_run_workers(run_id, "local-test-workspace"), self.assertRaisesRegex(RuntimeError, "已停止"):
            client.model_info()
        self.assertEqual(requests, [])

    def test_model_errors_keep_existing_business_classification(self):
        """转发错误码正确投影为原有超时、连接及 HTTP 错误。"""
        for error, expected in (
            (ModelTransmitError("timeout", code="UPSTREAM_TIMEOUT"), MODEL_REQUEST_TIMEOUT),
            (ModelTransmitError("connection", code="UPSTREAM_CONNECTION_ERROR"), MODEL_CONNECTION_FAILED),
            (ModelTransmitError("auth", code="UPSTREAM_AUTH_ERROR", status_code=401), MODEL_HTTP_ERROR),
        ):
            with self.subTest(code=error.code):
                failure = classify_unit_model_failure(error, stage="model_invoke")
                self.assertEqual(failure.code, expected)

    def test_only_transport_failure_enters_existing_retry_loop(self):
        """已有重试只重试网络错误，认证错误仍直接上抛。"""
        calls = []

        def invoke():
            """前两次网络失败，第三次成功。"""
            calls.append(1)
            if len(calls) < 3:
                raise ModelTransmitError("connection", code="UPSTREAM_CONNECTION_ERROR")
            return "ok"

        with patch("app.services.model_transport_retry.time.sleep"):
            self.assertEqual(run_with_transport_retry(invoke), "ok")
        self.assertEqual(len(calls), 3)
        auth = ModelTransmitError("auth", code="UPSTREAM_AUTH_ERROR", status_code=401)
        with self.assertRaises(ModelTransmitError), patch("app.services.model_transport_retry.time.sleep") as delay:
            run_with_transport_retry(lambda: (_ for _ in ()).throw(auth))
        delay.assert_not_called()
