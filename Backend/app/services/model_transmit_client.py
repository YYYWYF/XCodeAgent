"""通过官方 AG-UI 事件模型与标准 SSE 客户端访问模型转发服务。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4
from weakref import WeakKeyDictionary

import httpx
from ag_ui.core import Event, RunAgentInput
from httpx_sse import aconnect_sse, connect_sse
from pydantic import TypeAdapter, ValidationError

_EVENT_ADAPTER = TypeAdapter(Event)


class ModelTransmitError(RuntimeError):
    """保留模型依赖故障信息，避免将上游认证错误当作 Studio 登录失效。"""

    def __init__(self, message: str, *, code: str = "MODEL_TRANSMIT_ERROR",
                 status_code: int | None = None, retryable: bool = False):
        """建立独立异常并保留转发错误码、状态与重试属性。"""
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.provider = "transmit"
        self.model = "studio-model"


class _RunEvents:
    """校验单次 AG-UI 流的生命周期和结构化模型结果。"""

    def __init__(self, payload: dict[str, Any], result_name: str):
        """冻结本次 Run 身份，防止串流事件污染模型结果。"""
        self.thread_id = payload["threadId"]
        self.run_id = payload["runId"]
        self.result_name = result_name
        self.started = False
        self.finished = False
        self.has_result = False

    def consume(self, data: str) -> tuple[str, dict[str, Any]] | None:
        """使用官方联合事件类型校验消息，并只交付模型专用原始数据。"""
        try:
            event = _EVENT_ADAPTER.validate_json(data)
        except ValidationError as exc:
            raise ModelTransmitError("模型转发服务返回无效 AG-UI 事件", code="INVALID_AGUI_EVENT") from exc
        kind = event.type
        if self.finished:
            raise ModelTransmitError("模型流在结束后继续输出事件", code="INVALID_RUN_LIFECYCLE")
        if kind == "RUN_STARTED":
            if self.started or event.run_id != self.run_id or event.thread_id != self.thread_id:
                raise ModelTransmitError("模型流运行身份不匹配", code="INVALID_RUN_LIFECYCLE")
            self.started = True
            return None
        if not self.started:
            raise ModelTransmitError("模型流缺少 RUN_STARTED", code="INVALID_RUN_LIFECYCLE")
        if kind == "RUN_ERROR":
            raise ModelTransmitError(event.message, code=event.code or "MODEL_RUN_ERROR")
        if kind == "STATE_SNAPSHOT":
            self._check_outcome(event.snapshot)
        if kind == "RUN_FINISHED":
            if event.run_id != self.run_id or event.thread_id != self.thread_id:
                raise ModelTransmitError("模型流结束身份不匹配", code="INVALID_RUN_LIFECYCLE")
            self._check_outcome(getattr(event, "result", None))
            self.finished = True
        if kind == "CUSTOM":
            if event.name == "model-inference.error":
                value = event.value if isinstance(event.value, dict) else {}
                raise ModelTransmitError(value.get("message", "模型调用失败"),
                                         code=value.get("code", "MODEL_RUN_ERROR"),
                                         status_code=value.get("statusCode"),
                                         retryable=bool(value.get("retryable", False)))
            if event.name in (self.result_name, "model-inference.chunk"):
                if not isinstance(event.value, dict):
                    raise ModelTransmitError("模型结果结构无效", code="INVALID_MODEL_RESULT")
                self._check_outcome(event.value)
                if event.name == self.result_name:
                    self.has_result = True
                    return "result", event.value
                return "chunk", event.value
        # 官方文本及工具事件用于协议展示；模型内容只消费 CUSTOM，避免重复拼接。
        return None

    @staticmethod
    def _check_outcome(value: Any) -> None:
        """拒绝结束快照或结果中的失败状态。"""
        if isinstance(value, dict) and (value.get("status") in ("failed", "error", "cancelled")
                                        or value.get("error")):
            error = value.get("error")
            details = error if isinstance(error, dict) else {}
            raise ModelTransmitError(details.get("message", "模型调用未成功完成"),
                                     code=details.get("code", "MODEL_RUN_FAILED"),
                                     status_code=details.get("statusCode"))

    def validate_end(self) -> None:
        """流连接结束后要求合法终帧和结构化结果均已到达。"""
        if not self.finished or not self.has_result:
            raise ModelTransmitError("模型流不完整：缺少结束事件或模型结果", code="INCOMPLETE_MODEL_STREAM")


class ModelTransmitClient:
    """无隐式重试的同步和异步 AG-UI 客户端，连接随单次调用关闭。"""

    def __init__(self, base_url: str, token: str = "", *, trust_env: bool = False,
                 transport: Any = None):
        """保存服务端连接信息；transport 仅用于标准 HTTP 测试注入。"""
        self.base_url = base_url.rstrip("/")
        self.headers = {"Accept": "text/event-stream"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.trust_env = trust_env
        self.transport = transport
        self._model_info: dict[str, Any] | None = None
        self._sync_info_lock = threading.Lock()
        self._lock_registry_guard = threading.Lock()
        self._async_info_locks = WeakKeyDictionary()

    @staticmethod
    def payload(thread_id: str, run_id: str, forwarded: dict[str, Any]) -> dict[str, Any]:
        """通过官方 RunAgentInput 生成请求；原始模型消息完整保留在 forwardedProps。"""
        return RunAgentInput(thread_id=thread_id, run_id=run_id, messages=[], tools=[],
                             context=[], state={}, forwarded_props=forwarded).model_dump(by_alias=True)

    def _options(self, timeout: float) -> dict[str, Any]:
        """统一 HTTP 配置，重试预算仅交给转发服务处理。"""
        return {"base_url": self.base_url, "headers": self.headers, "timeout": timeout,
                "trust_env": self.trust_env, "transport": self.transport}

    @staticmethod
    def _check_http(response: httpx.Response) -> None:
        """把传输状态转换为模型依赖异常，不向 Studio 登录层抛 HTTPException。"""
        if response.is_error:
            raise ModelTransmitError(f"模型转发服务 HTTP {response.status_code}",
                                     code="MODEL_TRANSMIT_HTTP_ERROR", status_code=response.status_code,
                                     retryable=response.status_code in (429, 502, 503, 504))

    def iterate(self, path: str, payload: dict[str, Any], *, timeout: float = 120,
                result_name: str = "model-inference.result") -> Iterator[tuple[str, dict[str, Any]]]:
        """以标准同步 SSE 消费事件并确保连接、解析器随完成或取消关闭。"""
        state = _RunEvents(payload, result_name)
        try:
            with httpx.Client(**self._options(timeout)) as client:
                with connect_sse(client, "POST", path, json=payload) as source:
                    self._check_http(source.response)
                    for event in source.iter_sse():
                        item = state.consume(event.data)
                        if item is not None:
                            yield item
            state.validate_end()
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelTransmitError("模型转发连接或事件读取失败", code="MODEL_TRANSMIT_TRANSPORT_ERROR",
                                     retryable=True) from exc

    async def aiterate(self, path: str, payload: dict[str, Any], *, timeout: float = 120,
                       result_name: str = "model-inference.result") -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """以标准异步 SSE 消费事件，取消会退出上下文并关闭 HTTP 响应。"""
        state = _RunEvents(payload, result_name)
        try:
            async with httpx.AsyncClient(**self._options(timeout)) as client:
                async with aconnect_sse(client, "POST", path, json=payload) as source:
                    self._check_http(source.response)
                    async for event in source.aiter_sse():
                        item = state.consume(event.data)
                        if item is not None:
                            yield item
            state.validate_end()
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelTransmitError("模型转发连接或事件读取失败", code="MODEL_TRANSMIT_TRANSPORT_ERROR",
                                     retryable=True) from exc

    def model_info(self, *, timeout: float = 120) -> dict[str, Any]:
        """首次真正调用时查询并缓存公开信息，构造和导入过程不访问网络。"""
        with self._sync_info_lock:
            if self._model_info is None:
                run_id = str(uuid4())
                payload = self.payload(run_id, run_id, {})
                for kind, value in self.iterate("/model-info/run", payload, timeout=timeout,
                                                result_name="model-info.result"):
                    if kind == "result":
                        result = value
                self._model_info = self._validate_info(result)
        return dict(self._model_info)

    @staticmethod
    def _validate_info(value: dict[str, Any]) -> dict[str, Any]:
        """校验公开目录最小契约，防止空模型身份被永久缓存。"""
        if (not isinstance(value.get("model"), str) or not value["model"].strip()
                or value.get("provider") != "openai" or value.get("upstream") not in ("direct", "gateway")):
            raise ModelTransmitError("模型公开信息结构无效", code="INVALID_MODEL_INFO")
        return dict(value)

    async def amodel_info(self, *, timeout: float = 120) -> dict[str, Any]:
        """异步懒加载公开信息，并在同一事件循环内合并并发首次查询。"""
        if self._model_info is not None:
            return dict(self._model_info)
        # 缓存模型可能由不同线程的事件循环调用；锁只归属其创建的循环。
        loop = asyncio.get_running_loop()
        with self._lock_registry_guard:
            lock = self._async_info_locks.setdefault(loop, asyncio.Lock())
        async with lock:
            if self._model_info is None:
                run_id = str(uuid4())
                payload = self.payload(run_id, run_id, {})
                async for kind, value in self.aiterate("/model-info/run", payload, timeout=timeout,
                                                      result_name="model-info.result"):
                    if kind == "result":
                        result = value
                self._model_info = self._validate_info(result)
        return dict(self._model_info)
