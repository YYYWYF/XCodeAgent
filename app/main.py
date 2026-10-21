"""FastAPI 服务入口，所有模型产品动作采用官方 AG-UI。"""

import secrets
from collections.abc import AsyncIterator
from typing import Any

from ag_ui.core import CustomEvent, RunAgentInput, TextMessageContentEvent
from ag_ui.encoder import EventEncoder
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from .config import Settings
from .contracts import InferenceInput
from .history import DuplicateRunError, HistoryStore
from .inference import InferenceService
from .protocol import end_events, start_events
from .tracing import TraceRecorder
from .upstream import Upstream, connection


async def read_envelope(request: Request, settings: Settings) -> RunAgentInput:
    """先鉴权并限制输入大小，再以官方模型校验 AG-UI 调用身份。"""
    expected = f"Bearer {settings.service_token.get_secret_value()}"
    if not secrets.compare_digest(request.headers.get("authorization", "").encode(), expected.encode()):
        raise HTTPException(401, "转发服务访问凭证无效")
    body = bytearray()
    async for fragment in request.stream():
        body.extend(fragment)
        if len(body) > settings.max_request_bytes:
            raise HTTPException(413, "模型调用请求超出大小限制")
    try:
        envelope = RunAgentInput.model_validate_json(body)
        for identity in (envelope.thread_id, envelope.run_id):
            if not identity.strip() or len(identity) > 1024 or "\r" in identity or "\n" in identity:
                raise ValueError("threadId 和 runId 必须是有效单行身份")
        return envelope
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, "AG-UI 输入或调用身份无效") from exc


def stream_events(events: AsyncIterator[Any], accept: str | None) -> StreamingResponse:
    """通过官方编码器输出事件，禁用代理缓冲以保留实时增量。"""
    encoder = EventEncoder(accept=accept)

    async def encoded() -> AsyncIterator[str]:
        """保持迭代器关闭传播，保证客户端取消能触发历史收尾。"""
        try:
            async for event in events:
                yield encoder.encode(event)
        finally:
            await events.aclose()

    return StreamingResponse(encoded(), media_type=encoder.get_content_type(),
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def create_app(settings: Settings | None = None, upstream: Upstream | None = None,
               tracing: TraceRecorder | None = None) -> FastAPI:
    """创建独立服务；使用 uvicorn app.main:create_app --factory 启动。"""
    settings = settings or Settings()
    history = HistoryStore(settings.history_db_path)
    service = InferenceService(settings, history, upstream or Upstream(settings), tracing or TraceRecorder(settings))
    app = FastAPI(title="DevAgent Model Transmit", version="1.0.0")
    app.state.history, app.state.settings, app.state.service = history, settings, service

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """提供无秘密的进程健康及公开协议元数据，不访问上游。"""
        return {"status": "ok", "service": "model-transmit", "model": settings.active_model,
                **settings.gateway_metadata,
                "upstream": settings.model_upstream, "langsmithEnabled": settings.langsmith_enabled,
                "protocol": "AG-UI", "endpoints": {"modelInfo": "/model-info/run", "modelInference": "/model-inference/run"}}

    @app.post("/model-info/run")
    async def model_info(request: Request) -> StreamingResponse:
        """返回当前公开模型信息，绝不返回连接地址和凭证。"""
        envelope = await read_envelope(request, settings)

        async def events() -> AsyncIterator[Any]:
            """为只读模型信息查询生成完整 AG-UI 生命周期。"""
            for event in start_events(envelope.thread_id, envelope.run_id):
                yield event
            yield TextMessageContentEvent(message_id=f"{envelope.run_id}:assistant", delta=settings.active_model)
            yield CustomEvent(name="model-info.result", value={"model": settings.active_model, "provider": "openai",
                                                               "upstream": settings.model_upstream, **settings.gateway_metadata})
            for event in end_events(envelope.thread_id, envelope.run_id, "completed"):
                yield event

        return stream_events(events(), request.headers.get("accept"))

    @app.post("/model-inference/run")
    async def model_inference(request: Request) -> StreamingResponse:
        """校验纯模型输入、防重登记并建立可取消的模型事件流。"""
        envelope = await read_envelope(request, settings)
        try:
            props = envelope.forwarded_props or {}
            raw_call = props.get("modelInference")
            if isinstance(raw_call, dict):
                raw_call = {"timeoutSeconds": settings.default_timeout_seconds, **raw_call}
            call = InferenceInput.model_validate(raw_call)
            if call.timeout_seconds > settings.max_timeout_seconds or call.max_retries > settings.max_retries:
                raise ValueError("模型超时或重试预算超过服务端上限")
            connection(settings, call, envelope.thread_id, envelope.run_id)
        except (ValidationError, ValueError, AttributeError) as exc:
            message = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
            raise HTTPException(422, settings.redact(message)) from exc
        try:
            record = service.reserve(envelope, call)
        except DuplicateRunError as exc:
            raise HTTPException(409, str(exc)) from exc
        return stream_events(service.events(envelope, call, record), request.headers.get("accept"))

    return app
