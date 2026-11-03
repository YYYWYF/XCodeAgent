"""使用真实 SDK 加模拟 HTTP 验证两种上游的独立转发和观测。"""

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from ag_ui.core import RunAgentInput
from fastapi.testclient import TestClient

from app.aggregation import CompletionAccumulator
from app.contracts import InferenceInput
from app.main import create_app
from app.observation_output import ObservationOutput
from app.tracing import TraceRecorder
from app.upstream import Upstream, connection
from tests.helpers import chunk, completion, envelope, read_events, settings, sse_response


class ServiceTests(unittest.TestCase):
    """验证 API 鉴权、参数完整性、事件生命周期与失败语义。"""

    def app_client(self, handler, **overrides):
        """构建真实 OpenAI SDK 与离线 MockTransport 服务。"""
        config = settings(**overrides)
        observations = []
        self.observations = observations
        output = ObservationOutput(enabled=config.model_observation_output_enabled,
                                   writer=lambda payload: observations.append(json.loads(payload)))
        app = create_app(config, Upstream(config, httpx.MockTransport(handler)), observation_output=output)
        self.addCleanup(app.state.observation_output.close)
        client = TestClient(app)
        self.addCleanup(client.close)
        return app, client

    def test_stream_text_tools_usage_and_observation(self):
        """保留文本及工具参数分片，输出实际模型的完成状态和用量。"""
        captured = []

        def handler(request):
            """记录真实 SDK 请求并生成工具 JSON 参数分片。"""
            captured.append(request)
            return sse_response([
                chunk({"role": "assistant", "content": "你"}), chunk({"content": "好"}),
                chunk({"tool_calls": [{"index": 0, "id": "call-1", "type": "function", "function": {"name": "search", "arguments": ""}}]}),
                chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"q":'}}]}),
                chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"a"}'}}]}),
                chunk(finish_reason="tool_calls", usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}),
            ])

        app, client = self.app_client(handler)
        events = read_events(client, envelope(metadata={"sessionId": "session-1", "userId": "u1", "extensions": {"future": 1}}))
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertEqual("".join(event["delta"] for event in events if event["type"] == "TEXT_MESSAGE_CONTENT"), "你好")
        self.assertEqual("".join(event["delta"] for event in events if event["type"] == "TOOL_CALL_ARGS"), '{"q":"a"}')
        result = next(event["value"] for event in events if event.get("name") == "model-inference.result")
        self.assertEqual(result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"], '{"q":"a"}')
        self.assertTrue(app.state.observation_output.drain(timeout=3))
        record = self.observations[0]
        self.assertEqual(record["data"]["status"], "completed")
        self.assertEqual(record["identity"]["user_id"], "u1")
        self.assertEqual(record["data"]["model"]["total_tokens"], 5)
        self.assertEqual(result["usage"]["total_tokens"], 5)
        sent = json.loads(captured[0].content)
        self.assertEqual(sent["model"], app.state.settings.active_model)
        self.assertEqual(sent["thinking"], {"type": "disabled"})
        self.assertEqual(captured[0].headers["authorization"], "Bearer test-direct-secret")
        self.assertNotIn("x-gateway-key", captured[0].headers)
        self.assertNotIn("test-direct-secret", json.dumps(record))

    def test_repeated_run_id_forwards_each_complete_context_and_observes_each_call(self):
        """同 runId 的流式和非流式请求均独立执行，各自使用完整上下文和新 Span。"""
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                called = []

                def handler(request):
                    called.append(json.loads(request.content))
                    if streaming:
                        return sse_response([chunk({"content": "你好"}), chunk(finish_reason="stop", usage={"total_tokens": 5})])
                    return httpx.Response(200, json=completion())

                app, client = self.app_client(handler)
                for content in ("第一次请求", "第二次请求"):
                    raw = envelope(streaming=streaming)
                    messages = [{"role": "system", "content": "保持调用方上下文"},
                                {"role": "user", "content": content}]
                    raw["forwardedProps"]["modelInference"]["request"]["messages"] = messages
                    events = read_events(client, raw)
                    self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                    result = next(event["value"] for event in events if event.get("name") == "model-inference.result")
                    self.assertEqual(result["usage"]["total_tokens"], 5)
                    self.assertEqual(called[-1]["messages"], messages)
                self.assertEqual(len(called), 2)
                self.assertTrue(app.state.observation_output.drain(timeout=3))
                self.assertEqual(len(self.observations), 2)
                self.assertEqual({record["identity"]["run_id"] for record in self.observations}, {"run-1"})
                self.assertEqual(len({record["data"]["span_id"] for record in self.observations}), 2)

    def test_gateway_headers_fixed_credentials(self):
        """网关认证始终来自服务端，调用元数据只影响三个身份请求头。"""
        captured = []

        def handler(request):
            """截取网关完整地址、模型和扩展认证头。"""
            captured.append(request)
            return httpx.Response(200, json=completion())

        _, client = self.app_client(handler, model_upstream="gateway", gateway_api_key="test-gateway-secret", gateway_key="test-gateway-key")
        read_events(client, envelope(streaming=False, metadata={"spaid": "user123", "sessionId": "session-abc", "requestId": "req-demo-003"}))
        request = captured[0]
        self.assertEqual(str(request.url), "http://lt5404-agent-gateway.paas.cmbchina.cn/pattern/llm/fast/v1/chat/completions")
        self.assertEqual(request.headers["authorization"], "Bearer test-gateway-secret")
        self.assertEqual(request.headers["x-gateway-key"], "test-gateway-key")
        self.assertEqual(request.headers["x-spaid"], "user123")
        self.assertEqual(request.headers["x-session-id"], "session-abc")
        self.assertEqual(request.headers["x-request-id"], "req-demo-003")
        self.assertEqual(json.loads(request.content)["model"], "fast")

    def test_gateway_fixed_spaid_and_inactive_validation(self):
        """未启用网关允许空参数和未完成地址；固定身份不可由请求覆盖。"""
        config = settings(gateway_fast_base_url="not-configured")
        self.assertEqual(config.model_upstream, "direct")
        config = settings(model_upstream="gateway", gateway_api_key="g-secret", gateway_key="g-key", gateway_spaid_source="fixed", gateway_spaid="fixed-user")
        _, _, headers = connection(config, InferenceInput.model_validate(envelope(metadata={"spaid": "caller"})["forwardedProps"]["modelInference"]), "t", "r")
        self.assertEqual(headers["x-spaid"], "fixed-user")
        self.assertEqual(headers["x-session-id"], "t")
        with self.assertRaises(ValueError):
            settings(model_upstream="gateway")

    def test_failed_upstream_observation_and_error_redaction(self):
        """上游认证错误独立于 Studio 登录，秘密不出现在事件或观测中。"""
        def handler(request):
            """在错误正文回显假密钥，验证服务清理规则。"""
            return httpx.Response(401, json={"error": {"message": "bad test-direct-secret", "type": "authentication_error"}})

        app, client = self.app_client(handler)
        events = read_events(client, envelope())
        error = next(event["value"] for event in events if event.get("name") == "model-inference.error")
        self.assertEqual(error["code"], "UPSTREAM_AUTH_ERROR")
        self.assertTrue(app.state.observation_output.drain(timeout=3))
        self.assertEqual(self.observations[0]["data"]["status"], "failed")
        self.assertNotIn("test-direct-secret", json.dumps(events))
        self.assertNotIn("test-direct-secret", json.dumps(self.observations))
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")

    def test_incomplete_stream_preserves_partial(self):
        """上游提前结束不能假报完成，已产生的文本仍交付客户端。"""
        def handler(request):
            """返回只有内容而没有结束原因的流。"""
            return sse_response([chunk({"content": "部分"})])

        app, client = self.app_client(handler)
        events = read_events(client, envelope())
        self.assertTrue(any(event.get("name") == "model-inference.error" for event in events))
        self.assertTrue(app.state.observation_output.drain(timeout=3))
        self.assertEqual(self.observations[0]["data"]["status"], "failed")
        self.assertEqual("".join(event["delta"] for event in events if event["type"] == "TEXT_MESSAGE_CONTENT"), "部分")

    def test_auth_health_and_model_info_no_secrets(self):
        """健康与目录不调用上游，产品入口鉴权且不公开地址和凭证。"""
        def handler(request):
            """目录与健康如访问上游就让测试失败。"""
            raise AssertionError("不允许访问上游")

        _, client = self.app_client(handler)
        self.assertEqual(client.post("/model-info/run", json=envelope()).status_code, 401)
        self.assertEqual(client.get("/health").status_code, 200)
        response = client.post("/model-info/run", json=envelope(), headers={"Authorization": "Bearer local-model-transmit-dev-token"})
        self.assertIn("model-info.result", response.text)
        self.assertNotIn("test-direct-secret", response.text)
        self.assertNotIn("http://", response.text)

    def test_forbidden_header_override_and_input_limit(self):
        """拒绝连接覆盖与过大输入，验证发生在任何上游调用之前。"""
        def handler(request):
            """无效输入不能触发上游。"""
            raise AssertionError("不允许访问上游")

        _, client = self.app_client(handler)
        body = envelope()
        body["forwardedProps"]["modelInference"]["request"]["extra_headers"] = {"Authorization": "attacker"}
        headers = {"Authorization": "Bearer local-model-transmit-dev-token"}
        self.assertEqual(client.post("/model-inference/run", json=body, headers=headers).status_code, 422)
        _, limited = self.app_client(handler, max_request_bytes=30)
        self.assertEqual(limited.post("/model-inference/run", json=envelope(), headers=headers).status_code, 413)

    def test_invalid_nonstream_not_completed(self):
        """空 choices 或缺少结束原因的非流式响应均不得写为成功。"""
        for value in ({"choices": []}, {"choices": [{"message": {"content": "partial"}}]}):
            accumulator = CompletionAccumulator("model")
            accumulator.add(value)
            self.assertFalse(accumulator.complete)


class CancellationAndTracingTests(unittest.IsolatedAsyncioTestCase):
    """直接控制异步生成器以验证取消和追踪隔离。"""

    async def test_cancel_closes_upstream_and_finishes_trace_with_partial_output(self):
        """取消时立即关闭上游生成器，并将部分结果用于本地追踪收尾。"""
        config = settings()
        closed = []

        class FakeUpstream:
            """仅产出一个模型增量的离线生成器。"""

            async def invoke(self, call, thread_id, run_id, observation=None):
                """在 finally 中记录连接关闭证据。"""
                try:
                    yield chunk({"content": "部分"})
                    await asyncio.Event().wait()
                finally:
                    closed.append(True)

        app = create_app(config, FakeUpstream())
        self.addCleanup(app.state.observation_output.close)
        raw = envelope()
        run = RunAgentInput.model_validate(raw)
        call = InferenceInput.model_validate(raw["forwardedProps"]["modelInference"])
        events = app.state.service.events(run, call)
        with patch.object(app.state.service.tracing, "finish", wraps=app.state.service.tracing.finish) as finish:
            async for event in events:
                if getattr(event, "name", None) == "model-inference.chunk":
                    break
            await events.aclose()
        finish.assert_awaited_once()
        self.assertEqual(finish.await_args.args[2], "cancelled")
        self.assertIn("部分", json.dumps(finish.await_args.args[1], ensure_ascii=False))
        self.assertEqual(closed, [True])

    async def test_local_tracing_needs_no_client_and_creation_failure_isolated(self):
        """默认无需密钥也生成本地追踪，SDK 创建失败不阻断主服务。"""
        with patch("langsmith.run_trees.get_cached_client", side_effect=AssertionError("unexpected client")):
            recorder = TraceRecorder(settings())
            span_id, tree = await recorder.start({}, {})
            self.assertEqual(span_id, str(tree.id))
            self.assertIsNone(tree.ls_client)
        with patch("app.tracing.RunTree", side_effect=RuntimeError("local SDK failure")):
            recorder = TraceRecorder(settings(langsmith_enabled=True))
            self.assertEqual(await recorder.start({}, {}), (None, None))

    async def test_tracing_mock_records_input_output_and_faults(self):
        """Mock 覆盖本地追踪收尾及失败隔离，确认永远不调用上传方法。"""
        recorder = TraceRecorder(settings())
        with patch("app.tracing.RunTree") as factory:
            trace_id, tree = await recorder.start({"messages": []}, {"thread_id": "s"})
            await recorder.finish(tree, {"choices": []}, "completed", None)
            self.assertIsNotNone(trace_id)
            factory.return_value.end.assert_called_once()
            factory.return_value.post.assert_not_called()
            factory.return_value.patch.assert_not_called()
            self.assertEqual(factory.call_args.kwargs["inputs"], {"messages": []})
            factory.return_value.end.side_effect = RuntimeError("local SDK failure")
            with self.assertLogs("app.tracing", level="WARNING"):
                await recorder.finish(tree, {}, "failed", {"code": "UPSTREAM_TIMEOUT"})

    async def test_real_trace_sdk_keeps_input_output_only_in_local_object(self):
        """使用真实 RunTree 保存输入输出和状态，阻止所有上传与客户端创建。"""
        recorder = TraceRecorder(settings(langsmith_enabled=True))
        with patch("langsmith.RunTree.post", side_effect=AssertionError("unexpected upload")), \
             patch("langsmith.RunTree.patch", side_effect=AssertionError("unexpected upload")), \
             patch("langsmith.run_trees.get_cached_client", side_effect=AssertionError("unexpected client")):
            trace_id, tree = await recorder.start({"messages": [{"role": "user", "content": "hello"}]},
                                                  {"thread_id": "trace-session"})
            self.assertIsNotNone(tree)
            await recorder.finish(tree, {"choices": []}, "completed", None)
        self.assertIsNone(tree.ls_client)
        self.assertEqual(str(tree.id), trace_id)
        self.assertEqual(tree.metadata["status"], "completed")
        self.assertEqual(tree.outputs, {"choices": []})


if __name__ == "__main__":
    unittest.main()
