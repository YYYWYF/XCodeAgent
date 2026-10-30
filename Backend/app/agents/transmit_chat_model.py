"""Studio 的统一 LangChain 模型适配层，不新增本地历史与恢复机制。"""

from __future__ import annotations

from app.agents.model_business_context import current_model_context

from collections.abc import AsyncIterator, Iterator, Sequence
from contextvars import ContextVar
from copy import deepcopy
import math
from typing import Any
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableConfig, ensure_config
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai.chat_models.base import _convert_message_to_dict
from pydantic import ConfigDict, Field, PrivateAttr

from app.agents.transmit_model_messages import completion_result, final_stream_chunk, generation_chunk
from app.services.model_transmit_client import ModelTransmitClient, ModelTransmitError

_OPEN_STREAMS: ContextVar[list[Any] | None] = ContextVar("model_transmit_open_streams", default=None)


class TransmitChatModel(BaseChatModel):
    """把 LangChain 调用转换为统一模型 AG-UI 流，业务上下文完全由调用方提供。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    transmit_url: str
    transmit_token: str = Field(default="", exclude=True, repr=False)
    temperature: float | None = 0.7
    max_tokens: int | None = None
    max_retries: int = 2
    timeout_seconds: float = 120
    extra_body: dict[str, Any] | None = None
    trust_env: bool = False
    spaid: str | None = None
    transport: Any = Field(default=None, exclude=True, repr=False)
    _transmit_client: ModelTransmitClient | None = PrivateAttr(default=None)

    @property
    def _llm_type(self) -> str:
        """提供 LangChain 跟踪中的模型适配器类型。"""
        return "model-transmit"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        """仅公开无密钥的连接身份，不把认证信息写入日志或 LangSmith。"""
        return {"transmit_url": self.transmit_url}

    def _client(self) -> ModelTransmitClient:
        """延迟创建客户端，使模型构造及 Studio 登录启动无需网络连接。"""
        if self._transmit_client is None:
            self._transmit_client = ModelTransmitClient(self.transmit_url, self.transmit_token,
                                                        trust_env=self.trust_env, transport=self.transport)
        return self._transmit_client

    def _call_context(self, config: RunnableConfig | None) -> dict[str, Any]:
        """逐调用读取继承的 Runnable 配置，禁止将会话身份写入缓存模型实例。"""
        resolved = ensure_config(config)
        metadata = resolved.get("metadata", {})
        configurable = resolved.get("configurable", {})
        run_id = str(uuid4())
        thread_id = str(configurable.get("thread_id") or metadata.get("session_id") or run_id)
        result = {"sessionId": thread_id, "requestId": run_id}
        for source, target in (("user_id", "userId"), ("project_id", "projectId"),
                               ("studio_data_ref", "studioDataRef"), ("extensions", "extensions")):
            if source in metadata:
                result[target] = deepcopy(metadata[source])
        # 单次模型请求 UUID 继续用于 code8 防重；前端业务轮次保留在扩展中，不能混用。
        extensions = result.setdefault("extensions", {})
        business_context = current_model_context(metadata)
        if business_context:
            extensions["context"] = business_context
        else:
            extensions.pop("context", None)
        for key, target in (("run_id", "studio_run_id"), ("workflow_scope", "workflow_scope"),
                            ("operation_id", "operation_id"), ("conversation_id", "conversation_id")):
            if isinstance(metadata.get(key), str) and metadata[key]:
                extensions[target] = metadata[key]
        spaid = metadata.get("spaid", self.spaid)
        if spaid:
            result["spaid"] = spaid
        return result

    def invoke(self, input: Any, config: RunnableConfig | None = None, *, stop=None, **kwargs: Any):
        """为同步单次调用注入独立身份并保留 LangChain 标准回调流程。"""
        kwargs.setdefault("_transmit_context", self._call_context(config))
        return super().invoke(input, config, stop=stop, **kwargs)

    async def ainvoke(self, input: Any, config: RunnableConfig | None = None, *, stop=None, **kwargs: Any):
        """为异步单次调用注入独立身份，不跨并发调用共享会话上下文。"""
        kwargs.setdefault("_transmit_context", self._call_context(config))
        return await super().ainvoke(input, config, stop=stop, **kwargs)

    def stream(self, input: Any, config: RunnableConfig | None = None, *, stop=None, **kwargs: Any):
        """逐调用创建身份并使用父类的标准流式聚合与回调。"""
        streams = []
        token = _OPEN_STREAMS.set(streams)
        kwargs.setdefault("_transmit_context", self._call_context(config))
        try:
            yield from super().stream(input, config, stop=stop, **kwargs)
        finally:
            for stream in streams:
                stream.close()
            _OPEN_STREAMS.reset(token)

    async def astream(self, input: Any, config: RunnableConfig | None = None, *, stop=None, **kwargs: Any):
        """逐调用创建异步流身份，并保证调用方关闭时关闭内部生成器。"""
        streams = []
        token = _OPEN_STREAMS.set(streams)
        kwargs.setdefault("_transmit_context", self._call_context(config))
        iterator = super().astream(input, config, stop=stop, **kwargs)
        try:
            async for chunk in iterator:
                yield chunk
        finally:
            await iterator.aclose()
            for stream in streams:
                await stream.aclose()
            _OPEN_STREAMS.reset(token)

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any):
        """复用官方工具转换器，并保留工具选择及严格 JSON Schema 参数。"""
        strict = kwargs.pop("strict", None)
        formatted = [convert_to_openai_tool(tool, strict=strict) for tool in tools]
        if tool_choice is not None:
            names = {tool.get("function", {}).get("name") for tool in formatted}
            if tool_choice == "any" or tool_choice is True:
                tool_choice = "required"
            elif tool_choice is False:
                tool_choice = "none"
            elif isinstance(tool_choice, str) and tool_choice in names:
                tool_choice = {"type": "function", "function": {"name": tool_choice}}
            kwargs["tool_choice"] = tool_choice
        return self.bind(tools=formatted, **kwargs)

    def _request(self, messages: list[BaseMessage], stop: list[str] | None,
                 stream: bool, kwargs: dict[str, Any]) -> tuple[dict[str, Any], float]:
        """原样构造模型参数与调用预算；只移除适配器内部的传输字段。"""
        options = dict(kwargs)
        context = options.pop("_transmit_context", None) or self._call_context(None)
        try:
            timeout = float(options.pop("timeout", options.pop("timeout_seconds", self.timeout_seconds)))
        except (TypeError, ValueError) as exc:
            raise ModelTransmitError("调用超时必须为正数", code="INVALID_CALL_BUDGET") from exc
        retries = options.pop("max_retries", self.max_retries)
        if not math.isfinite(timeout) or timeout <= 0 or isinstance(retries, bool) \
                or not isinstance(retries, int) or retries < 0:
            raise ModelTransmitError("调用超时必须为正数，重试预算必须为非负整数", code="INVALID_CALL_BUDGET")
        body = deepcopy(self.extra_body or {})
        body.update(deepcopy(options.pop("extra_body", None) or {}))
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        if stop is not None:
            body["stop"] = stop
        body.update(options)
        # 模型和消息只由服务端配置及本次标准消息输入确定，扩展参数不得覆盖。
        body.update({"model": "studio-model", "messages": [_convert_message_to_dict(m) for m in messages],
                     "stream": stream})
        if "max_completion_tokens" in options:
            body.pop("max_tokens", None)
        if stream:
            body["stream_options"] = {**body.get("stream_options", {}), "include_usage": True}
        forwarded = {"modelInference": {"request": body, "timeoutSeconds": timeout,
                                         "maxRetries": retries, "metadata": context}}
        payload = self._client().payload(context["sessionId"], context["requestId"], forwarded)
        # SSE 包含上游重试和收尾；HTTP 读取窗口应覆盖服务端允许的各次尝试。
        return payload, max(1, timeout) * (max(0, retries) + 1) + 30

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        """同步等待完整 AG-UI 生命周期后交付正式模型消息。"""
        self._client().model_info(timeout=self.timeout_seconds)
        payload, timeout = self._request(messages, stop, False, kwargs)
        result = None
        for kind, value in self._client().iterate("/model-inference/run", payload, timeout=timeout):
            if kind == "result":
                result = value
        if result is None:
            raise ModelTransmitError("缺少模型完整结果", code="INVALID_MODEL_RESULT")
        return completion_result(result)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        """异步等待完整 AG-UI 生命周期，取消会关闭请求连接。"""
        await self._client().amodel_info(timeout=self.timeout_seconds)
        payload, timeout = self._request(messages, stop, False, kwargs)
        result = None
        async for kind, value in self._client().aiterate("/model-inference/run", payload, timeout=timeout):
            if kind == "result":
                result = value
        if result is None:
            raise ModelTransmitError("缺少模型完整结果", code="INVALID_MODEL_RESULT")
        return completion_result(result)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        """同步返回模型增量，最终聚合结果不再次输出已收到的内容。"""
        self._client().model_info(timeout=self.timeout_seconds)
        payload, timeout = self._request(messages, stop, True, kwargs)
        had_chunks = had_usage = had_finish = False
        result = None
        iterator = self._client().iterate("/model-inference/run", payload, timeout=timeout)
        if _OPEN_STREAMS.get() is not None:
            _OPEN_STREAMS.get().append(iterator)
        try:
            for kind, value in iterator:
                if kind == "result":
                    result = value
                    continue
                chunk = generation_chunk(value)
                if chunk is not None:
                    had_chunks = True
                    had_usage = had_usage or chunk.message.usage_metadata is not None
                    had_finish = had_finish or bool((chunk.generation_info or {}).get("finish_reason"))
                    if run_manager:
                        run_manager.on_llm_new_token(chunk.text, chunk=chunk)
                    yield chunk
            if result is not None:
                chunk = final_stream_chunk(result, had_chunks=had_chunks, had_usage=had_usage, had_finish=had_finish)
                if chunk is not None:
                    yield chunk
        finally:
            iterator.close()

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        """异步返回模型与工具参数增量，关闭时释放下层 HTTP 流。"""
        await self._client().amodel_info(timeout=self.timeout_seconds)
        payload, timeout = self._request(messages, stop, True, kwargs)
        had_chunks = had_usage = had_finish = False
        result = None
        iterator = self._client().aiterate("/model-inference/run", payload, timeout=timeout)
        if _OPEN_STREAMS.get() is not None:
            _OPEN_STREAMS.get().append(iterator)
        try:
            async for kind, value in iterator:
                if kind == "result":
                    result = value
                    continue
                chunk = generation_chunk(value)
                if chunk is not None:
                    had_chunks = True
                    had_usage = had_usage or chunk.message.usage_metadata is not None
                    had_finish = had_finish or bool((chunk.generation_info or {}).get("finish_reason"))
                    if run_manager:
                        await run_manager.on_llm_new_token(chunk.text, chunk=chunk)
                    yield chunk
            if result is not None:
                chunk = final_stream_chunk(result, had_chunks=had_chunks, had_usage=had_usage, had_finish=had_finish)
                if chunk is not None:
                    yield chunk
        finally:
            await iterator.aclose()
