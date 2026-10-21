"""使用真实 SDK 加模拟 HTTP 验证两种上游与持久历史。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from ag_ui.core import RunAgentInput
from fastapi.testclient import TestClient

from app.aggregation import CompletionAccumulator
from app.contracts import InferenceInput
from app.history import HistoryStore
from app.main import create_app
from app.tracing import TraceRecorder
from app.upstream import Upstream, connection
from tests.helpers import chunk, completion, envelope, read_events, settings, sse_response


class ServiceTests(unittest.TestCase):
    """验证 API 鉴权、参数完整性、事件生命周期与失败语义。"""

    def setUp(self):
        """每例使用独立临时数据库，绝不写入源码目录或真实配置。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)

    def app_client(self, handler, **overrides):
        """构建真实 OpenAI SDK 与离线 MockTransport 服务。"""
        config = settings(self.path, **overrides)
        app = create_app(config, Upstream(config, httpx.MockTransport(handler)))
        return app, TestClient(app)

    def test_stream_text_tools_usage_and_history(self):
        """保留文本及工具参数分片，并把实际输入和完成结果落库。"""
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

        app, client = self.app_client(handler, mock_analytics_enabled=True)
        events = read_events(client, envelope(metadata={"sessionId": "session-1", "userId": "u1", "extensions": {"future": 1}}))
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertEqual("".join(event["delta"] for event in events if event["type"] == "TEXT_MESSAGE_CONTENT"), "你好")
        self.assertEqual("".join(event["delta"] for event in events if event["type"] == "TOOL_CALL_ARGS"), '{"q":"a"}')
        result = next(event["value"] for event in events if event.get("name") == "model-inference.result")
        self.assertEqual(result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"], '{"q":"a"}')
        record = app.state.history.get("run-1")
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["session_id"], "session-1")
        self.assertEqual(record["model_output"], result)
        self.assertEqual(record["usage"]["total_tokens"], 5)
        self.assertFalse(record["analysis"]["receipt"]["inserted"])
        self.assertEqual(record["extensions"], {"future": 1})
        sent = json.loads(captured[0].content)
        self.assertEqual(sent["model"], app.state.settings.active_model)
        self.assertEqual(sent["thinking"], {"type": "disabled"})
        self.assertEqual(captured[0].headers["authorization"], "Bearer test-direct-secret")
        self.assertNotIn("x-gateway-key", captured[0].headers)
        self.assertNotIn("test-direct-secret", json.dumps(record))

    def test_nonstream_and_duplicate_no_replay(self):
        """非流式保留完整响应，重复 runId 直接拒绝且不再调用上游。"""
        called = []

        def handler(request):
            """模拟一次完整响应。"""
            called.append(request)
            return httpx.Response(200, json=completion())

        app, client = self.app_client(handler)
        events = read_events(client, envelope(streaming=False))
        self.assertTrue(any(event["type"] == "TOOL_CALL_START" for event in events))
        self.assertEqual(app.state.history.get("run-1")["model_output"]["choices"][0]["finish_reason"], "tool_calls")
        response = client.post("/model-inference/run", json=envelope(streaming=False), headers={"Authorization": "Bearer local-model-transmit-dev-token"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(called), 1)

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
        config = settings(self.path, gateway_fast_base_url="not-configured")
        self.assertEqual(config.model_upstream, "direct")
        config = settings(self.path, model_upstream="gateway", gateway_api_key="g-secret", gateway_key="g-key", gateway_spaid_source="fixed", gateway_spaid="fixed-user")
        _, _, headers = connection(config, InferenceInput.model_validate(envelope(metadata={"spaid": "caller"})["forwardedProps"]["modelInference"]), "t", "r")
        self.assertEqual(headers["x-spaid"], "fixed-user")
        self.assertEqual(headers["x-session-id"], "t")
        with self.assertRaises(ValueError):
            settings(self.path, model_upstream="gateway")

    def test_failed_upstream_history_and_error_redaction(self):
        """上游认证错误独立于 Studio 登录，秘密不出现在事件或历史中。"""
        def handler(request):
            """在错误正文回显假密钥，验证服务清理规则。"""
            return httpx.Response(401, json={"error": {"message": "bad test-direct-secret", "type": "authentication_error"}})

        app, client = self.app_client(handler)
        events = read_events(client, envelope())
        error = next(event["value"] for event in events if event.get("name") == "model-inference.error")
        self.assertEqual(error["code"], "UPSTREAM_AUTH_ERROR")
        self.assertEqual(app.state.history.get("run-1")["status"], "failed")
        self.assertNotIn("test-direct-secret", json.dumps(events))
        self.assertNotIn("test-direct-secret", json.dumps(app.state.history.get("run-1")))
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")

    def test_incomplete_stream_preserves_partial(self):
        """上游提前结束不能假报完成，已产生文本保留用于分析。"""
        def handler(request):
            """返回只有内容而没有结束原因的流。"""
            return sse_response([chunk({"content": "部分"})])

        app, client = self.app_client(handler)
        events = read_events(client, envelope())
        self.assertTrue(any(event.get("name") == "model-inference.error" for event in events))
        record = app.state.history.get("run-1")
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["model_output"]["choices"][0]["message"]["content"], "部分")

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


class PersistenceAndTracingTests(unittest.IsolatedAsyncioTestCase):
    """直接控制异步生成器以验证取消和追踪隔离。"""

    async def test_cancel_closes_upstream_and_persists(self):
        """取消时同步结束数据库记录，并立即关闭上游生成器。"""
        with tempfile.TemporaryDirectory() as directory:
            config = settings(Path(directory))
            closed = []

            class FakeUpstream:
                """仅产出一个模型增量的离线生成器。"""

                async def invoke(self, call, thread_id, run_id):
                    """在 finally 中记录连接关闭证据。"""
                    try:
                        yield chunk({"content": "部分"})
                        await asyncio.Event().wait()
                    finally:
                        closed.append(True)

            app = create_app(config, FakeUpstream())
            raw = envelope()
            run = RunAgentInput.model_validate(raw)
            call = InferenceInput.model_validate(raw["forwardedProps"]["modelInference"])
            record = app.state.service.reserve(run, call)
            events = app.state.service.events(run, call, record)
            async for event in events:
                if getattr(event, "name", None) == "model-inference.chunk":
                    break
            await events.aclose()
            self.assertEqual(closed, [True])
            self.assertEqual(app.state.history.get("run-1")["status"], "cancelled")
            self.assertIn("部分", json.dumps(app.state.history.get("run-1"), ensure_ascii=False))

    async def test_restart_marks_only_unfinished_interrupted(self):
        """进程重启保留上下文与部分产出，遗留运行标记为 interrupted。"""
        with tempfile.TemporaryDirectory() as directory:
            config = settings(Path(directory))
            app = create_app(config)
            raw = envelope()
            run = RunAgentInput.model_validate(raw)
            call = InferenceInput.model_validate(raw["forwardedProps"]["modelInference"])
            app.state.service.reserve(run, call)
            app.state.history.checkpoint("run-1", {"choices": [{"message": {"content": "部分"}}]}, "trace-id")
            restarted = HistoryStore(config.history_db_path)
            self.assertEqual(restarted.get("run-1")["status"], "interrupted")
            self.assertEqual(restarted.get("run-1")["langsmith_run_id"], "trace-id")
            self.assertEqual(restarted.get("run-1")["input_context"]["messages"], call.request["messages"])

    async def test_tracing_disabled_and_client_failure_isolated(self):
        """关闭追踪零客户端零网络，初始化失败也不阻断主服务。"""
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.tracing.Client") as client:
                recorder = TraceRecorder(settings(Path(directory)))
                self.assertEqual(await recorder.start({}, {}), (None, None))
                client.assert_not_called()
            with patch("app.tracing.Client", side_effect=RuntimeError("offline")):
                recorder = TraceRecorder(settings(Path(directory), langsmith_enabled=True, langsmith_api_key="trace-key"))
                self.assertEqual(await recorder.start({}, {}), (None, None))

    async def test_tracing_mock_records_input_output_and_faults(self):
        """Mock 覆盖 LangSmith 生命周期及失败路径，全程不访问外网。"""
        with tempfile.TemporaryDirectory() as directory:
            recorder = TraceRecorder(settings(Path(directory), langsmith_enabled=True, langsmith_api_key="trace-key"), client=MagicMock())
            with patch("app.tracing.RunTree") as factory:
                trace_id, tree = await recorder.start({"messages": []}, {"thread_id": "s"})
                await recorder.finish(tree, {"choices": []}, "completed", None)
                self.assertIsNotNone(trace_id)
                factory.return_value.post.assert_called_once()
                factory.return_value.patch.assert_called_once()
                self.assertEqual(factory.call_args.kwargs["inputs"], {"messages": []})
                factory.return_value.post.side_effect = RuntimeError("offline")
                self.assertIsNone((await recorder.start({}, {}))[1])

    async def test_real_trace_sdk_writes_only_to_injected_mock_client(self):
        """使用真实 RunTree 校验 SDK 参数契约，客户端替身阻止任何外网请求。"""
        with tempfile.TemporaryDirectory() as directory:
            client = MagicMock()
            recorder = TraceRecorder(settings(Path(directory), langsmith_enabled=True,
                                               langsmith_api_key="trace-key"), client=client)
            trace_id, tree = await recorder.start({"messages": [{"role": "user", "content": "hello"}]},
                                                  {"thread_id": "trace-session"})
            self.assertIsNotNone(tree)
            await recorder.finish(tree, {"choices": []}, "completed", None)
            client.create_run.assert_called_once()
            client.update_run.assert_called_once()
            self.assertEqual(str(tree.id), trace_id)
            self.assertEqual(tree.metadata["status"], "completed")


if __name__ == "__main__":
    unittest.main()
