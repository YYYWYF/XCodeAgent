"""真实 SDK 消息转换与 LangChain 流式回调入口的离线回归。"""

import asyncio
import json
import unittest
from unittest.mock import Mock, patch

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import _convert_delta_to_message_chunk
from langsmith import tracing_context

from app.agents.transmit_chat_model import TransmitChatModel
from app.observability.model_stream_timing import has_effective_model_delta


class DeltaProbe(BaseCallbackHandler):
    """记录有效增量个数供断言，生产代码不保存正文。"""

    run_inline = True

    def __init__(self):
        """建立单次测试的回调计数。"""
        self.effective = 0

    def on_llm_new_token(self, token, *, chunk=None, **kwargs):
        """使用生产判定器检查 SDK 实际交付的增量。"""
        self.effective += int(has_effective_model_delta(token, chunk))


class ModelStreamAdapterTests(unittest.TestCase):
    """覆盖 OpenAI 和 Transmit 的同步异步、流式和完整响应。"""

    def test_openai_sync_async_stream_and_nonstream(self):
        """真实 OpenAI HTTP 解析后仅文本/工具参数计时，非流式没有首增量。"""
        completion = {"id": "c", "object": "chat.completion", "model": "test", "created": 0,
                      "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
        frames = [{"role": "assistant", "content": ""}, {"content": " "},
                  {"tool_calls": [{"index": 0, "id": "id", "type": "function", "function": {"name": "lookup", "arguments": ""}}]},
                  {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}]

        def respond(request):
            """按请求标志提供离线 OpenAI SSE 或完整 JSON。"""
            if not json.loads(request.content).get("stream"):
                return httpx.Response(200, json=completion)
            rows = [{"id": "c", "model": "test", "object": "chat.completion.chunk", "created": 0,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": None}]} for delta in frames]
            rows.append({"id": "c", "model": "test", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            return httpx.Response(200, text="".join("data: " + json.dumps(row) + "\n\n" for row in rows) + "data: [DONE]\n\n",
                                  headers={"content-type": "text/event-stream"})

        async def exercise():
            """在同一事件循环内关闭异步客户端，不遗留资源。"""
            with httpx.Client(transport=httpx.MockTransport(respond)) as sync_client:
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as async_client:
                    for streaming in (True, False):
                        for asynchronous in (True, False):
                            probe = DeltaProbe()
                            model = ChatOpenAI(model="test", api_key="fixture", streaming=streaming,
                                               http_client=sync_client, http_async_client=async_client, callbacks=[probe])
                            if asynchronous:
                                await model.ainvoke("test")
                            else:
                                model.invoke("test")
                            self.assertEqual(probe.effective, 3 if streaming else 0)
        with tracing_context(enabled=False):
            asyncio.run(exercise())

    def test_transmit_sync_async_stream_final_only_and_nonstream(self):
        """官方 stream/astream 回调合成 Chunk 时，仅完整结果也不产生首增量。"""
        result = {"model": "test", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
        delta = {"choices": [{"index": 0, "delta": {"content": "ok"}}]}
        for streaming in (True, False):
            for real_delta in (True, False):
                for asynchronous in (True, False):
                    probe = DeltaProbe()
                    client = Mock()
                    events = ([('chunk', delta)] if real_delta else []) + [('result', result)]
                    def sync_iterate(*args, **kwargs):
                        """真实同步客户端返回可关闭生成器。"""
                        yield from events

                    client.iterate.side_effect = sync_iterate

                    async def iterate(*args, **kwargs):
                        """模拟既有客户端异步事件，保留适配层处理顺序。"""
                        for item in events:
                            yield item

                    async def info(*args, **kwargs):
                        """模拟无网络的模型元数据检查。"""
                        return {}

                    client.aiterate = iterate
                    client.amodel_info = info
                    model = TransmitChatModel(transmit_url="https://example.test", callbacks=[probe])
                    with tracing_context(enabled=False), patch.object(TransmitChatModel, "_client", return_value=client):
                        if streaming and asynchronous:
                            async def consume():
                                """消费真实 LangChain 异步流以触发官方回调。"""
                                return [chunk async for chunk in model.astream("test")]
                            chunks = asyncio.run(consume())
                            self.assertEqual("".join(chunk.text for chunk in chunks), "ok")
                        elif streaming:
                            self.assertEqual("".join(chunk.text for chunk in model.stream("test")), "ok")
                        elif asynchronous:
                            self.assertEqual(asyncio.run(model.ainvoke("test")).content, "ok")
                        else:
                            self.assertEqual(model.invoke("test").content, "ok")
                    self.assertEqual(probe.effective, int(streaming and real_delta))

    def test_sdk_real_converters_and_final_aggregate(self):
        """锁定版本 SDK 的 thinking/tool 增量有效，签名和完整结果补帧无效。"""
        from langchain_anthropic import ChatAnthropic
        from anthropic.types import RawContentBlockDeltaEvent
        delta = _convert_delta_to_message_chunk({"tool_calls": [{"index": 0, "id": "id",
                    "function": {"name": "lookup", "arguments": ""}}]}, AIMessageChunk)
        self.assertTrue(has_effective_model_delta("", ChatGenerationChunk(message=delta)))
        anthropic = ChatAnthropic(model="claude-test", api_key="fixture")
        for data, expected in (({"type": "thinking_delta", "thinking": "thought"}, True),
                               ({"type": "signature_delta", "signature": "signed"}, False),
                               ({"type": "input_json_delta", "partial_json": "{"}, True)):
            message, _ = anthropic._make_message_chunk_from_anthropic_event(
                RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=data),
                coerce_content_to_string=False)
            self.assertEqual(has_effective_model_delta(message.content, ChatGenerationChunk(message=message)), expected)
        model = ChatOpenAI(api_key="fixture", model="test")
        final = model._get_generation_chunk_from_completion({"model": "test", "choices": [
            {"message": {"role": "assistant", "content": "whole"}, "finish_reason": "stop"}]})
        self.assertFalse(has_effective_model_delta(final.text, final))

    def test_anthropic_sync_async_stream_and_nonstream(self):
        """真实 Anthropic thinking/tool 事件通过官方入口，完整消息无流式观测。"""
        from langchain_anthropic import ChatAnthropic
        from anthropic.types import Message, RawContentBlockDeltaEvent
        events = [RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=delta) for delta in (
            {"type": "signature_delta", "signature": "signed"},
            {"type": "thinking_delta", "thinking": "thought"},
            {"type": "input_json_delta", "partial_json": "{"},
        )]
        complete = Message(id="m", type="message", role="assistant", model="claude-test",
                           content=[{"type": "text", "text": "ok"}], stop_reason="end_turn",
                           usage={"input_tokens": 2, "output_tokens": 1})
        for streaming in (True, False):
            for asynchronous in (True, False):
                probe = DeltaProbe()
                model = ChatAnthropic(model="claude-test", api_key="fixture", callbacks=[probe],
                                      thinking={"type": "enabled", "budget_tokens": 1024})

                async def async_events():
                    """提供真实 SDK 事件的异步迭代器。"""
                    for event in events:
                        yield event

                async def create(*args, **kwargs):
                    """按模式返回异步事件或完整 SDK 消息。"""
                    return async_events() if streaming else complete

                with tracing_context(enabled=False), patch.object(ChatAnthropic, "_create", return_value=iter(events) if streaming else complete), patch.object(
                    ChatAnthropic, "_acreate", side_effect=create,
                ):
                    if streaming and asynchronous:
                        async def consume():
                            """经真实 LangChain astream 交付结构化增量。"""
                            return [chunk async for chunk in model.astream("test")]
                        asyncio.run(consume())
                    elif streaming:
                        list(model.stream("test"))
                    elif asynchronous:
                        asyncio.run(model.ainvoke("test"))
                    else:
                        model.invoke("test")
                self.assertEqual(probe.effective, 2 if streaming else 0)
