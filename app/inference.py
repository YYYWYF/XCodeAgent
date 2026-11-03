"""逐请求转发模型调用，编排本地追踪、观测及 AG-UI 输出。"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

from ag_ui.core import CustomEvent, RunAgentInput
from openai import APIConnectionError, APIStatusError, APITimeoutError
import httpx

from .aggregation import CompletionAccumulator
from .config import Settings
from .contracts import InferenceInput
from .model_observation import ModelObservation, observe_safely
from .observation_output import ObservationOutput
from .protocol import ResponseProjection, end_events, start_events
from .tracing import TraceRecorder
from .upstream import Upstream, request_body

logger = logging.getLogger(__name__)


def inference_error(exc: Exception, settings: Settings) -> dict[str, Any]:
    """转换传输错误并清除服务秘密，避免误触发 Studio 登录状态。"""
    if isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
        code, retryable = "UPSTREAM_TIMEOUT", True
    elif isinstance(exc, (APIConnectionError, httpx.TransportError)):
        code, retryable = "UPSTREAM_CONNECTION_ERROR", True
    elif isinstance(exc, APIStatusError):
        code = "UPSTREAM_AUTH_ERROR" if exc.status_code in {401, 403} else "UPSTREAM_RATE_LIMIT" if exc.status_code == 429 else "UPSTREAM_ERROR"
        retryable = exc.status_code == 429 or exc.status_code >= 500
    else:
        code, retryable = "UPSTREAM_PROTOCOL_ERROR", False
    result = {"code": code, "message": settings.redact(str(exc)), "retryable": retryable}
    if isinstance(exc, APIStatusError):
        result["statusCode"] = exc.status_code
    return result


class InferenceService:
    """直接使用本次请求的完整上下文，各次调用独立执行和观测。"""

    def __init__(self, settings: Settings, upstream: Upstream, tracing: TraceRecorder,
                 observation_output: ObservationOutput):
        """显式注入网络、追踪和输出组件以便离线验证。"""
        self.settings, self.upstream, self.tracing = settings, upstream, tracing
        self.observation_output = observation_output

    async def events(self, envelope: RunAgentInput, call: InferenceInput) -> AsyncIterator[Any]:
        """转发原始结果，统一完成成功、失败及取消的观测收尾。"""
        run_id, thread_id = envelope.run_id, envelope.thread_id
        accumulator = CompletionAccumulator(self.settings.active_model)
        projection = ResponseProjection(run_id)
        status, error, tree = "failed", None, None
        observation = None
        try:
            observation = ModelObservation(envelope, call, self.settings)
        except Exception:
            logger.warning("模型埋点初始化失败，推理继续")
        try:
            for event in start_events(thread_id, run_id):
                yield event
            metadata = {"thread_id": call.metadata.session_id or thread_id, "run_id": run_id,
                        "request_id": call.metadata.request_id or run_id, "upstream": self.settings.model_upstream,
                        "model": self.settings.active_model, **self.settings.gateway_metadata}
            _, tree = await self.tracing.start(request_body(self.settings, call), metadata)
            observe_safely(observation, "set_trace", tree)
            async with aclosing(self.upstream.invoke(call, thread_id, run_id, observation=observation)) as responses:
                async for response in responses:
                    accumulator.add(response)
                    if call.request.get("stream", False):
                        for event in projection.chunk_events(response, accumulator):
                            yield event
            if not accumulator.complete:
                raise ValueError("上游流缺少有效的 finish_reason，模型输出不完整")
            status = "completed"
            for event in projection.result_events(accumulator, bool(call.request.get("stream", False))):
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            # 上游已经完成时，下游交付被关闭不能改写已完成的模型执行事实。
            if status != "completed":
                status = "cancelled"
                error = {"code": "REQUEST_CANCELLED", "message": "Studio 已取消或断开模型调用", "retryable": False}
            raise
        except Exception as exc:
            error = inference_error(exc, self.settings)
            for event in projection.close_tools(accumulator):
                yield event
            yield CustomEvent(name="model-inference.error", value=error)
        finally:
            # 本地 SDK 对象先收尾，data 才能读取最终输出、状态和错误；此处没有远程上传。
            try:
                await self.tracing.finish(tree, accumulator.output, status, error)
            except Exception:
                logger.warning("本地追踪收尾失败，继续输出当前观测")
            summary = self.build_observation(observation, status, error)
            # 开关及容量检查都在统一入口；主请求不等待打印/ES，不改已冻结的时间。
            if summary is not None:
                try:
                    self.observation_output.submit(summary)
                except Exception:
                    # 输出组件故障也不得改写模型结果。
                    pass
        for event in end_events(thread_id, run_id, status):
            yield event

    def build_observation(self, observation: ModelObservation | None, status: str,
                          error: dict[str, Any] | None) -> dict[str, Any] | None:
        """在本地追踪收尾后生成独立快照，当前方法不执行控制台或 ES I/O。"""
        if observation is None:
            return None
        try:
            return observation.snapshot(status, error)
        except Exception:
            logger.warning("模型埋点构造失败，推理继续")
            return None
