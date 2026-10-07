"""通过真实 SDK 和本地 MockTransport 验证 OpenCode Go 请求头，不调用付费服务。"""

import asyncio
from dataclasses import replace
import json
import os
import unittest
from unittest.mock import patch
from uuid import UUID

import anthropic
import httpx2 as anthropic_httpx
import httpx
import openai
from langchain_core.runnables import RunnableLambda

from app.agents.model_factory import create_chat_model
from app.config import Settings


class OpenCodeGoHeaderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        """准备两个协议的本地 HTTP 客户端和请求记录。"""

        self.requests = []
        self.sync_client = self.enterContext(httpx.Client(transport=httpx.MockTransport(self._respond)))
        self.async_client = httpx.AsyncClient(transport=httpx.MockTransport(self._respond))
        self.anthropic_sync_client = self.enterContext(anthropic_httpx.Client(transport=anthropic_httpx.MockTransport(self._respond)))
        self.anthropic_async_client = anthropic_httpx.AsyncClient(transport=anthropic_httpx.MockTransport(self._respond))
        self.settings = Settings(
            model_base_url="https://example.com/v1", model_api_key="test-key",
            model_name="test-model", model_max_retries=0, default_max_tokens=128,
        )

    async def asyncTearDown(self) -> None:
        """关闭异步客户端，避免测试留下 HTTP 资源。"""

        await self.async_client.aclose()
        await self.anthropic_async_client.aclose()

    def _respond(self, request: httpx.Request) -> httpx.Response:
        """根据实际 SDK 请求返回两个协议各自的普通或流式响应。"""

        self.requests.append(request)
        response_class = anthropic_httpx.Response if isinstance(request, anthropic_httpx.Request) else httpx.Response
        body = json.loads(request.content)
        if request.url.path.endswith("messages"):
            message = {
                "id": "msg-test", "type": "message", "role": "assistant", "model": "test-model",
                "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            events = [
                {"type": "message_start", "message": {**message, "content": [], "stop_reason": None}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}},
                {"type": "message_stop"},
            ]
            sse = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
        else:
            message = {
                "id": "chat-test", "object": "chat.completion", "created": 0, "model": "test-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            }
            chunk = {
                **message, "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            }
            sse = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
        if body.get("stream"):
            return response_class(200, headers={"content-type": "text/event-stream"}, content=sse)
        return response_class(200, json=message)

    async def _model(self, provider: str = "openai", *, enabled: bool = True):
        """使用生产工厂创建模型，仅把 SDK 的实际网络出口替换成本地传输。"""

        model = create_chat_model(replace(
            self.settings, model_provider=provider, model_opencode_go_headers_enabled=enabled,
            model_custom_headers={"x-test-existing": "kept"},
        ))
        model.streaming = False
        if provider == "openai":
            # 先关闭工厂创建的客户端，再注入测试传输；不改生产配置与 SDK 请求逻辑。
            model.http_client.close()
            await model.http_async_client.aclose()
            model.client = openai.OpenAI(api_key="test-key", base_url=self.settings.model_base_url, http_client=self.sync_client).chat.completions
            model.async_client = openai.AsyncOpenAI(api_key="test-key", base_url=self.settings.model_base_url, http_client=self.async_client).chat.completions
        else:
            model._client = anthropic.Anthropic(api_key="test-key", base_url=self.settings.model_base_url, default_headers=model.default_headers, http_client=self.anthropic_sync_client)
            model._async_client = anthropic.AsyncAnthropic(api_key="test-key", base_url=self.settings.model_base_url, default_headers=model.default_headers, http_client=self.anthropic_async_client)
        return model

    def test_environment_switch_defaults_off_and_can_be_enabled(self) -> None:
        """未配置时关闭，只有显式环境开关才启用，并随 UI 模型配置视图保留。"""

        with patch.dict(os.environ, {"MODEL_BASE_URL": "https://example.com/v1", "MODEL_API_KEY": "test", "MODEL_NAME": "test"}, clear=True):
            self.assertFalse(Settings.from_env().model_opencode_go_headers_enabled)
            for value, expected in (("true", True), ("false", False)):
                os.environ["MODEL_OPENCODE_GO_HEADERS_ENABLED"] = value
                settings = Settings.from_env()
                self.assertEqual(settings.model_opencode_go_headers_enabled, expected)
                self.assertEqual(settings.for_ui_design_model().model_opencode_go_headers_enabled, expected)

    async def test_disabled_omits_headers_in_both_protocols_and_all_call_modes(self) -> None:
        """关闭时四种调用路径都不自动发送 Go session 或客户端身份请求头。"""

        for provider in ("openai", "anthropic"):
            model = await self._model(provider, enabled=False)
            config = {"configurable": {"thread_id": "ignored"}}
            model.invoke("hello", config=config)
            list(model.stream("hello", config=config))
            await model.ainvoke("hello", config=config)
            _ = [chunk async for chunk in model.astream("hello", config=config)]
        for request in self.requests:
            self.assertNotIn("x-opencode-session", request.headers)
            self.assertNotEqual(request.headers["User-Agent"], "devagentstudio/0.1")

    async def test_enabled_uses_stable_thread_across_calls_and_inherited_config(self) -> None:
        """两个协议在同步调用、工具绑定和嵌套 runnable 中使用同一会话 ID。"""

        for provider in ("openai", "anthropic"):
            model = await self._model(provider)
            config = {"configurable": {"thread_id": "thread-a"}}
            extras = {"x-test-call": "kept"}
            model.invoke("hello", config=config, extra_headers=extras)
            list(model.stream("hello", config=config))
            # 模拟 Graph 节点的嵌套调用：节点内部不需要再次传 config。
            RunnableLambda(lambda prompt: model.invoke(prompt)).invoke("hello", config=config)
            model.bind_tools([{"name": "test_tool", "description": "test", "parameters": {"type": "object", "properties": {}}}]).invoke("hello", config=config)
            self.assertEqual(extras, {"x-test-call": "kept"})
            self.assertEqual(self.requests[-4].headers["x-test-call"], "kept")
            if provider == "anthropic":
                self.assertEqual(self.requests[-1].headers["x-test-existing"], "kept")
        for request in self.requests:
            self.assertEqual(request.headers["x-opencode-session"], "thread-a")
            self.assertEqual(request.headers["User-Agent"], "devagentstudio/0.1")
            self.assertNotIn("extra_headers", json.loads(request.content))

    async def test_shared_model_concurrent_sessions_and_async_stream_do_not_leak(self) -> None:
        """同一缓存模型的并发异步请求及流式请求各自保留会话头。"""

        for provider in ("openai", "anthropic"):
            model = await self._model(provider)
            await asyncio.gather(*[
                model.ainvoke("hello", config={"configurable": {"thread_id": thread}})
                for thread in ("thread-a", "thread-b", "thread-a")
            ])
            self.assertCountEqual([r.headers["x-opencode-session"] for r in self.requests[-3:]], ["thread-a", "thread-b", "thread-a"])
            _ = [chunk async for chunk in model.astream("hello", config={"configurable": {"thread_id": "thread-c"}})]
            self.assertEqual(self.requests[-1].headers["x-opencode-session"], "thread-c")

    async def test_standalone_session_is_stable_and_unique_per_model(self) -> None:
        """无会话上下文时多轮请求共享实例 UUID，不同模型实例不共享它。"""

        for provider in ("openai", "anthropic"):
            model = await self._model(provider)
            model.invoke("hello")
            await model.ainvoke("hello")
            first = self.requests[-1].headers["x-opencode-session"]
            self.assertEqual(self.requests[-2].headers["x-opencode-session"], first)
            UUID(first)
            other = await self._model(provider)
            other.invoke("hello")
            self.assertNotEqual(self.requests[-1].headers["x-opencode-session"], first)
