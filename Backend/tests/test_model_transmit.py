"""以模拟 HTTP 上游验证模型转发接入，不访问真实模型或保存 Studio 历史。"""

import asyncio
import json
import unittest

import httpx
from ag_ui.core import Event
from ag_ui.encoder import EventEncoder
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from pydantic import TypeAdapter

from app.agents.transmit_chat_model import TransmitChatModel
from app.services.model_transmit_client import ModelTransmitError


def encode_events(events):
    """使用官方事件类型和编码器构造可供标准 SSE 客户端读取的测试数据。"""
    adapter = TypeAdapter(Event)
    encoder = EventEncoder()
    return "".join(encoder.encode(adapter.validate_python(event)) for event in events).encode()


class FakeRelay:
    """记录请求并输出官方 AG-UI 生命周期的可控模拟转发端。"""

    def __init__(self):
        """初始化调用记录及可注入的失败模式。"""
        self.requests = []
        self.failure = None
        self.tool_mode = False
        self.closed = []

    def handle(self, request):
        """响应模型目录和推理请求，保留所有字段供断言。"""
        payload = json.loads(request.content)
        self.requests.append((request, payload))
        start = {"type": "RUN_STARTED", "threadId": payload["threadId"], "runId": payload["runId"]}
        finish = {"type": "RUN_FINISHED", "threadId": payload["threadId"], "runId": payload["runId"]}
        if request.url.path == "/model-info/run":
            events = [start, {"type": "CUSTOM", "name": "model-info.result", "value": {
                "model": "fake-model", "provider": "openai", "upstream": "direct"}}, finish]
        else:
            if self.failure == "http":
                return httpx.Response(401, json={"detail": "invalid token"})
            if self.failure == "error":
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=encode_events([
                    start, {"type": "CUSTOM", "name": "model-inference.error", "value": {
                        "code": "UPSTREAM_AUTH_ERROR", "message": "上游认证失败", "statusCode": 401}}, finish]))
            body = payload["forwardedProps"]["modelInference"]["request"]
            message = {"role": "assistant", "content": "hello"}
            reason = "stop"
            if self.tool_mode:
                message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1",
                    "type": "function", "function": {"name": "lookup", "arguments": '{"x":1}'}}]}
                reason = "tool_calls"
            result = {"id": "completion-1", "model": "fake-model", "choices": [
                {"index": 0, "message": message, "finish_reason": reason}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}
            events = [start]
            if body.get("stream"):
                if self.tool_mode:
                    deltas = [{"role": "assistant", "tool_calls": [{"index": 0, "id": "call-1",
                        "type": "function", "function": {"name": "lookup", "arguments": '{"x":'}}]},
                        {"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]}]
                else:
                    deltas = [{"role": "assistant", "content": "hel"}, {"content": "lo"}]
                for delta in deltas:
                    events.append({"type": "CUSTOM", "name": "model-inference.chunk", "value": {
                        "id": "completion-1", "model": "fake-model", "choices": [{"index": 0,
                        "delta": delta, "finish_reason": None}]}})
                events.extend([
                    {"type": "TEXT_MESSAGE_START", "messageId": "m", "role": "assistant"},
                    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": "hello"},
                    {"type": "TEXT_MESSAGE_END", "messageId": "m"},
                    {"type": "CUSTOM", "name": "model-inference.chunk", "value": {
                        "model": "fake-model", "choices": [{"delta": {}, "finish_reason": reason}]}},
                    {"type": "CUSTOM", "name": "model-inference.chunk", "value": {
                        "choices": [], "usage": result["usage"]}},
                ])
            events.append({"type": "CUSTOM", "name": "model-inference.result", "value": result})
            if self.failure == "snapshot":
                events.append({"type": "STATE_SNAPSHOT", "snapshot": {"status": "failed"}})
            if self.failure == "run_error":
                events.append({"type": "RUN_ERROR", "message": "failed", "code": "FAILED"})
            if self.failure != "truncated":
                events.append(finish)
        stream = TrackingStream(encode_events(events), self.closed)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)


class TrackingStream(httpx.SyncByteStream, httpx.AsyncByteStream):
    """观察流提前关闭或取消时 HTTP 响应是否及时释放。"""

    def __init__(self, data, closed):
        """保存响应字节与资源关闭观测列表。"""
        self.data = data
        self.closed = closed

    def __iter__(self):
        """同步输出整个合法 SSE 字节流。"""
        yield self.data

    async def __aiter__(self):
        """异步输出整个合法 SSE 字节流。"""
        yield self.data

    def close(self):
        """记录同步响应已关闭。"""
        self.closed.append("sync")

    async def aclose(self):
        """记录异步响应已关闭。"""
        self.closed.append("async")


class ModelTransmitTests(unittest.TestCase):
    """覆盖同步调用、参数、完整性与工具分片。"""

    def setUp(self):
        """每个用例独立构造模型，防止目录缓存污染测试。"""
        self.relay = FakeRelay()
        self.model = TransmitChatModel(transmit_url="http://relay", transmit_token="access-token",
            max_tokens=80, max_retries=0, timeout_seconds=9, extra_body={"thinking": {"type": "disabled"}},
            transport=httpx.MockTransport(self.relay.handle))

    def test_lazy_nonstream_cache_parameters_and_unique_ids(self):
        """验证构造无网络、目录缓存、JSON绑定和每次调用独立身份。"""
        self.assertEqual(self.relay.requests, [])
        runnable = self.model.bind(max_tokens=5, response_format={"type": "json_object"},
                                   extra_body={"seed": 7})
        result = runnable.invoke("hi", {"configurable": {"thread_id": "session-1"}}, stop=["END"])
        self.assertEqual(result.content, "hello")
        self.assertEqual(result.response_metadata["finish_reason"], "stop")
        self.assertEqual(result.usage_metadata["total_tokens"], 6)
        self.model.invoke("again", {"configurable": {"thread_id": "session-1"}})
        self.assertEqual(len(self.relay.requests), 3)
        request, payload = self.relay.requests[1]
        props = payload["forwardedProps"]["modelInference"]
        body = props["request"]
        self.assertEqual(request.headers["Authorization"], "Bearer access-token")
        self.assertEqual(payload["threadId"], "session-1")
        self.assertNotEqual(payload["runId"], self.relay.requests[2][1]["runId"])
        self.assertEqual(props["maxRetries"], 0)
        self.assertEqual(props["timeoutSeconds"], 9)
        self.assertEqual(body["max_tokens"], 5)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["stop"], ["END"])
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["seed"], 7)
        self.assertNotIn("extra_body", body)
        self.assertNotIn("access-token", str(self.model))

    def test_stream_no_duplicate_content_usage_or_finish(self):
        """官方展示事件和终态聚合结果均不能重复模型内容或用量。"""
        chunks = list(self.model.stream("hi"))
        combined = sum(chunks[1:], chunks[0])
        self.assertEqual(combined.content, "hello")
        self.assertEqual(combined.usage_metadata["total_tokens"], 6)
        self.assertEqual(combined.response_metadata["finish_reason"], "stop")
        self.assertEqual(combined.response_metadata["model_name"], "fake-model")

    def test_bind_tools_preserves_argument_fragments(self):
        """验证原始工具参数分片及聚合后的函数调用一致。"""
        self.relay.tool_mode = True
        runnable = self.model.bind_tools([{"type": "function", "function": {"name": "lookup",
            "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}}], tool_choice="lookup")
        chunks = list(runnable.stream("hi"))
        fragments = [part["args"] for chunk in chunks for part in chunk.tool_call_chunks]
        self.assertEqual(fragments, ['{"x":', '1}'])
        combined = sum(chunks[1:], chunks[0])
        self.assertEqual(combined.tool_calls[0]["args"], {"x": 1})
        body = self.relay.requests[-1][1]["forwardedProps"]["modelInference"]["request"]
        self.assertEqual(body["tool_choice"], {"type": "function", "function": {"name": "lookup"}})

    def test_tool_history_and_multimodal_context_are_not_rebuilt(self):
        """消息历史及图片块由 Studio 提供，适配器不添加或重排内容。"""
        messages = [SystemMessage(content="system"),
                    HumanMessage(content=[{"type": "text", "text": "inspect"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abcd"}}]),
                    AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"x": 1}, "id": "call-1"}]),
                    ToolMessage(content='{"ok":true}', tool_call_id="call-1")]
        self.model.invoke(messages)
        body = self.relay.requests[-1][1]["forwardedProps"]["modelInference"]["request"]
        self.assertEqual([message["role"] for message in body["messages"]], ["system", "user", "assistant", "tool"])
        self.assertEqual(body["messages"][1]["content"][1]["type"], "image_url")
        self.assertEqual(body["messages"][3]["tool_call_id"], "call-1")
        self.assertEqual(messages[0].content, "system")

    def test_nested_runnable_inherits_thread_id(self):
        """LangGraph等父 Runnable 的上下文在内部未显式传config时仍可逐调用读取。"""
        runnable = RunnableLambda(self.model.invoke)
        runnable.invoke("hi", {"configurable": {"thread_id": "inherited-session"}})
        self.assertEqual(self.relay.requests[-1][1]["threadId"], "inherited-session")

    def test_disabled_stream_fallback_has_no_duplicate_internal_context(self):
        """父类禁用流式时会回调invoke，内部身份参数必须只传一次。"""
        self.model.disable_streaming = True
        chunks = list(self.model.stream("hi", {"configurable": {"thread_id": "fallback-session"}}))
        self.assertEqual(chunks[0].content, "hello")
        self.assertEqual(self.relay.requests[-1][1]["threadId"], "fallback-session")

    def test_invalid_call_budgets_fail_without_inference(self):
        """负数、非整数预算及非正超时清晰失败，不静默改变用户参数。"""
        for kwargs in ({"max_retries": -1}, {"max_retries": -0.1}, {"max_retries": True},
                       {"timeout_seconds": 0}, {"timeout_seconds": "bad"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ModelTransmitError) as caught:
                    self.model.invoke("hi", **kwargs)
                self.assertEqual(caught.exception.code, "INVALID_CALL_BUDGET")
        self.assertEqual(len(self.relay.requests), 1)

    def test_error_and_missing_terminal_are_failures(self):
        """HTTP认证、模型错误、失败快照、RUN_ERROR和断流均不能被当作成功。"""
        for failure in ("http", "error", "snapshot", "run_error", "truncated"):
            with self.subTest(failure=failure):
                self.relay.failure = failure
                with self.assertRaises(ModelTransmitError) as caught:
                    self.model.invoke("hi")
                if failure in ("http", "error"):
                    self.assertEqual(caught.exception.status_code, 401)
                self.assertEqual(caught.exception.provider, "transmit")

    def test_sync_early_close_releases_response(self):
        """同步消费者提前停止时立即释放响应连接。"""
        iterator = self.model.stream("hi")
        next(iterator)
        before = len(self.relay.closed)
        iterator.close()
        self.assertGreater(len(self.relay.closed), before)

    def test_stream_missing_terminal_raises_after_partial_content(self):
        """已经返回部分文字也必须因缺少终帧失败，不能将半截结果作为成功。"""
        self.relay.failure = "truncated"
        iterator = self.model.stream("hi")
        self.assertEqual(next(iterator).content, "hel")
        with self.assertRaises(ModelTransmitError) as caught:
            list(iterator)
        self.assertEqual(caught.exception.code, "INCOMPLETE_MODEL_STREAM")


class AsyncModelTransmitTests(unittest.IsolatedAsyncioTestCase):
    """覆盖异步并发会话隔离与取消清理。"""

    async def test_parallel_session_identity_and_info_cache(self):
        """同一缓存模型并行调用保持不同会话与请求身份。"""
        relay = FakeRelay()
        model = TransmitChatModel(transmit_url="http://relay", transport=httpx.MockTransport(relay.handle))
        results = await asyncio.gather(*(model.ainvoke("hi", {"configurable": {"thread_id": f"session-{i}"},
            "metadata": {"user_id": f"user-{i}"}}) for i in range(3)))
        self.assertTrue(all(result.content == "hello" for result in results))
        calls = [payload for request, payload in relay.requests if request.url.path == "/model-inference/run"]
        self.assertEqual({call["threadId"] for call in calls}, {"session-0", "session-1", "session-2"})
        self.assertEqual(len({call["runId"] for call in calls}), 3)
        for call in calls:
            context = call["forwardedProps"]["modelInference"]["metadata"]
            self.assertEqual(context["userId"], "user-" + call["threadId"].split("-")[-1])
        self.assertEqual(len(relay.requests), 4)

    async def test_async_stream_close_and_normal_result(self):
        """异步流提前关闭释放连接，完整读取时文本与用量不重复。"""
        relay = FakeRelay()
        model = TransmitChatModel(transmit_url="http://relay", transport=httpx.MockTransport(relay.handle))
        iterator = model.astream("hi")
        await anext(iterator)
        before = len(relay.closed)
        await iterator.aclose()
        self.assertGreater(len(relay.closed), before)
        chunks = [chunk async for chunk in model.astream("again")]
        combined = sum(chunks[1:], chunks[0])
        self.assertEqual(combined.content, "hello")
        self.assertEqual(combined.usage_metadata["total_tokens"], 6)

    async def test_async_error_is_not_success(self):
        """异步调用同样拒绝模型错误和缺少完整结束事件的结果。"""
        relay = FakeRelay()
        model = TransmitChatModel(transmit_url="http://relay", transport=httpx.MockTransport(relay.handle))
        for failure in ("error", "truncated"):
            relay.failure = failure
            with self.assertRaises(ModelTransmitError):
                await model.ainvoke("hi")


if __name__ == "__main__":
    unittest.main()
