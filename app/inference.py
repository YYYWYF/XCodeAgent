"""一次模型调用的防重、历史、追踪及 AG-UI 输出编排。"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any
from uuid import uuid4

from ag_ui.core import CustomEvent, RunAgentInput
from openai import APIConnectionError, APIStatusError, APITimeoutError
import httpx

from .aggregation import CompletionAccumulator
from .config import Settings
from .contracts import InferenceInput
from .history import HistoryStore, json_text, utc_now
from .model_observation import ModelObservation, observe_safely
from .observation_output import ObservationOutput
from .protocol import ResponseProjection, end_events, start_events
from .tracing import TraceRecorder, mock_analytics
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
    """只保存本次实际上下文，不读取旧上下文参与新的模型请求。"""

    def __init__(self, settings: Settings, history: HistoryStore, upstream: Upstream, tracing: TraceRecorder,
                 observation_output: ObservationOutput):
        """显式注入网络、存储和追踪组件以便离线验证。"""
        self.settings, self.history, self.upstream, self.tracing = settings, history, upstream, tracing
        self.observation_output = observation_output

    def reserve(self, envelope: RunAgentInput, call: InferenceInput) -> dict[str, Any]:
        """原子登记实际请求与服务端档位，重复 runId 明确冲突且调用方不能伪造档位。"""
        metadata = call.metadata
        request = request_body(self.settings, call)
        extensions = dict(metadata.extensions)
        extensions.pop("gatewayTier", None)
        extensions.update(self.settings.gateway_metadata)
        record = {
            "run_id": envelope.run_id, "thread_id": envelope.thread_id,
            "request_id": metadata.request_id or envelope.run_id, "attempt_id": str(uuid4()),
            "session_id": metadata.session_id or envelope.thread_id, "upstream_type": self.settings.model_upstream,
            "model_name": self.settings.active_model, "input_context": json_text(request),
            "status": "running", "started_at": utc_now(), "analysis": "{}",
            "extensions": json_text(extensions), "user_id": metadata.user_id,
            "project_id": metadata.project_id, "studio_data_ref": metadata.studio_data_ref,
        }
        self.history.reserve(record)
        return record

    async def events(self, envelope: RunAgentInput, call: InferenceInput, record: dict[str, Any]) -> AsyncIterator[Any]:
        """转发原始结果，统一完成成功、失败及取消的历史终结。"""
        run_id, thread_id = envelope.run_id, envelope.thread_id
        started = time.monotonic()
        last_checkpoint = started
        accumulator = CompletionAccumulator(self.settings.active_model)
        projection = ResponseProjection(run_id)
        status, error, span_id, tree = "failed", None, None, None
        observation = None
        try:
            observation = ModelObservation(envelope, call, self.settings)
        except Exception:
            logger.warning("模型埋点初始化失败，推理继续")
        try:
            for event in start_events(thread_id, run_id):
                yield event
            metadata = {"thread_id": record["session_id"], "run_id": run_id,
                        "request_id": record["request_id"], "upstream": record["upstream_type"],
                        "model": record["model_name"], **self.settings.gateway_metadata}
            span_id, tree = await self.tracing.start(json.loads(record["input_context"]), metadata)
            observe_safely(observation, "set_trace", tree)
            self.history.checkpoint(run_id, accumulator.output, span_id)
            async with aclosing(self.upstream.invoke(call, thread_id, run_id, observation=observation)) as responses:
                async for response in responses:
                    accumulator.add(response)
                    if time.monotonic() - last_checkpoint >= 0.5:
                        self.history.checkpoint(run_id, accumulator.output, span_id)
                        last_checkpoint = time.monotonic()
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
            duration_ms = int((time.monotonic() - started) * 1000)
            # 本地 SDK 对象先收尾，data 才能读取最终输出、状态和错误；此处没有远程上传。
            try:
                await self.tracing.finish(tree, accumulator.output, status, error)
            except Exception:
                logger.warning("本地追踪收尾失败，继续打印与保存当前观测")
            summary = self.build_observation(observation, status, error)
            analysis = {}
            if self.settings.mock_analytics_enabled:
                analysis = mock_analytics({**record, "extensions": json.loads(record["extensions"]),
                                           "status": status, "duration_ms": duration_ms, "usage": accumulator.output.get("usage")})
            if summary is not None:
                # 同份快照包含 identity.current_operation，复用既有 JSON 字段而不改表结构。
                analysis["execution_trace"] = summary
            # 本地历史保存与控制台完全相同的摘要，不向 LangSmith 发送数据。
            try:
                self.history.finish(run_id, status=status, output=accumulator.output, duration_ms=duration_ms,
                                    error=error, analysis=analysis, langsmith_run_id=span_id)
            finally:
                # 开关及容量检查都在统一入口；主请求不等待打印/ES，不改已冻结的时间。
                if summary is not None:
                    try:
                        self.observation_output.submit(summary)
                    except Exception:
                        # 输出组件故障也不得改写模型结果或历史保存的原有错误语义。
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
