"""逐调用采集模型用量与时间，不共享会话状态或读取旧历史补齐事实。"""

import logging
import time
from collections.abc import Callable
from typing import Any

from ag_ui.core import RunAgentInput

from .config import Settings
from .contracts import InferenceInput
from .history import utc_now
from .observation_models import ExecutionData, ExecutionError, ExecutionRecord, Identity, ModelUsage
from .operation_detection import detect_operation
from .trace_projection import (
    optional_identity, token_count, trace_duration, trace_metadata, trace_metric,
    trace_status, trace_time, trace_usage, utc_datetime,
)

logger = logging.getLogger(__name__)
TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


def nonempty_text(value: Any) -> bool:
    """识别非空文本和标准文本块，空格及换行也是有效生成内容。"""
    if isinstance(value, str):
        return len(value) > 0
    if isinstance(value, list):
        return any(
            isinstance(item, dict) and item.get("type") in {"text", "reasoning", "thinking", "refusal"}
            and any(nonempty_text(item.get(key)) for key in ("text", "reasoning", "thinking", "refusal"))
            for item in value
        )
    return False


def has_generated_output(response: dict[str, Any]) -> bool:
    """只观察生成的文本或工具名称参数，忽略角色、ID、结束帧和纯用量。"""
    for choice in response.get("choices") or []:
        delta = choice.get("delta") or {}
        if any(nonempty_text(delta.get(key)) for key in ("content", "reasoning_content", "reasoning", "refusal")):
            return True
        functions = [tool.get("function") or {} for tool in delta.get("tool_calls") or []]
        functions.append(delta.get("function_call") or {})
        if any(nonempty_text(function.get(key)) for function in functions for key in ("name", "arguments")):
            return True
    return False


def safe_error(error: dict[str, Any] | None) -> ExecutionError | None:
    """使用固定摘要，不将上游可能回显的 Prompt、源码和凭证写入埋点。"""
    if not error or error.get("code") == "REQUEST_CANCELLED":
        return None
    code = error.get("code")
    facts = {
        "UPSTREAM_TIMEOUT": ("transport", "上游模型请求超时"),
        "UPSTREAM_CONNECTION_ERROR": ("transport", "上游模型连接或响应读取失败"),
        "UPSTREAM_AUTH_ERROR": ("model", "上游模型认证失败"),
        "UPSTREAM_RATE_LIMIT": ("model", "上游模型请求被限流"),
        "UPSTREAM_ERROR": ("model", "上游模型返回 HTTP 错误"),
        "UPSTREAM_PROTOCOL_ERROR": ("validation", "上游模型响应不完整或协议无效"),
    }
    layer, message = facts.get(code, ("system", "模型调用执行失败"))
    return ExecutionError(layer=layer, code=optional_identity(code), message=message)


class ModelObservation:
    """保存单次上游调用的实际事实；日志响应体与 AG-UI 结果完全独立。"""

    def __init__(self, envelope: RunAgentInput, call: InferenceInput, settings: Settings,
                 clock: Callable[[], float] | None = None, wall_clock: Callable[[], str] | None = None):
        """冻结收到的身份和实际模型配置，为测试允许注入确定性时钟。"""
        metadata, extensions = call.metadata, call.metadata.extensions
        operation_extensions = dict(extensions)
        explicit = operation_extensions.get("current_operation")
        if isinstance(explicit, str) and settings.redact(explicit) != explicit:
            operation_extensions.pop("current_operation")
        self.identity = Identity(
            user_id=metadata.user_id, project_id=metadata.project_id,
            conversation_id=optional_identity(extensions.get("conversation_id")),
            thread_id=envelope.thread_id, run_id=envelope.run_id,
            operation_id=optional_identity(extensions.get("operation_id")),
            workflow_scope=optional_identity(extensions.get("workflow_scope")),
            current_operation=detect_operation(call.request, operation_extensions),
        )
        self.provider, self.model = settings.model_upstream, settings.active_model
        self.streaming = bool(call.request.get("stream", False))
        self._clock, self._wall_clock = clock or time.perf_counter, wall_clock or utc_now
        self.started_at = self.ended_at = None
        self.started_tick = self.ended_tick = self.first_output = self.last_output = None
        self.output_count = 0
        self.usage: dict[str, Any] = {}
        self.trace_tree = None

    def begin(self) -> None:
        """在实际 SDK 请求之前记录起点，不包括鉴权、登记和追踪准备。"""
        if self.started_tick is None:
            self.started_at, self.started_tick = self._wall_clock(), self._clock()
            self._sync_trace()

    def observe(self, response: dict[str, Any]) -> None:
        """保存最新有效用量快照并在收到分片时观测首末有效输出。"""
        usage = response.get("usage")
        if isinstance(usage, dict) and any(token_count(usage.get(key)) is not None for key in TOKEN_FIELDS):
            self.usage = dict(usage)
        if self.streaming and has_generated_output(response):
            now = self._clock()
            if self.first_output is None:
                self.first_output = now
            self.last_output = now
            self.output_count += 1
        self._sync_trace()

    def end(self) -> None:
        """上游读取结束或异常退出时冻结终点，后续交付和追踪不拉长耗时。"""
        if self.started_tick is not None and self.ended_tick is None:
            self.ended_at, self.ended_tick = self._wall_clock(), self._clock()
            self._sync_trace()

    def set_trace(self, tree: Any) -> None:
        """引用 SDK 本地追踪对象，收尾时再读取其最终事实，不提前冻结名称和类型。"""
        self.trace_tree = tree

    def _timings(self) -> tuple[int | None, int | None, int | None]:
        """计算本次真实单调时间，未知及无有效区间保持为空。"""
        duration = None if self.ended_tick is None else max(0, int((self.ended_tick - self.started_tick) * 1000))
        ttft = None if self.first_output is None or self.started_tick is None else max(0, int((self.first_output - self.started_tick) * 1000))
        output_duration = None
        if self.output_count > 1 and self.last_output > self.first_output:
            output_duration = max(0, int((self.last_output - self.first_output) * 1000))
        return duration, ttft, output_duration

    def _sync_trace(self) -> None:
        """只在内存中把实测时间和最新用量写入追踪对象，追踪故障不丢失本地事实。"""
        if self.trace_tree is None:
            return
        try:
            duration, ttft, output_duration = self._timings()
            if self.started_at is not None:
                self.trace_tree.start_time = utc_datetime(self.started_at)
            if self.ended_at is not None:
                self.trace_tree.end_time = utc_datetime(self.ended_at)
            self.trace_tree.add_metadata({
                "model_started": self.started_tick is not None,
                "duration_ms": duration, "ttft_ms": ttft, "output_duration_ms": output_duration,
            })
            if self.usage:
                self.trace_tree.set(usage_metadata=dict(zip(
                    ("input_tokens", "output_tokens", "total_tokens"),
                    (token_count(self.usage.get(key)) for key in TOKEN_FIELDS),
                )))
        except Exception:
            logger.warning("本地追踪事实更新失败，保留直接观测结果")

    def _error(self, metadata: dict[str, Any], status: str, error: dict[str, Any] | None) -> ExecutionError | None:
        """优先追踪中的结构化错误，只输出固定安全摘要，不直接回显 SDK 错误文本。"""
        if status == "cancelled":
            return None
        if "execution_error" in metadata:
            value = metadata["execution_error"]
            if value is None:
                return None
            if isinstance(value, dict):
                return safe_error(value)
        if getattr(self.trace_tree, "error", None):
            return safe_error(error or {"code": None})
        return safe_error(error)

    def snapshot(self, status: str, error: dict[str, Any] | None) -> dict[str, Any]:
        """优先读取本地追踪的最终数据，缺失有效事实才使用当前调用观测。"""
        tree, metadata = self.trace_tree, trace_metadata(self.trace_tree)
        status = trace_status(tree, metadata, status)
        input_tokens, output_tokens, total_tokens = trace_usage(tree, metadata, self.usage)
        if None not in (input_tokens, output_tokens, total_tokens) and total_tokens != input_tokens + output_tokens:
            total_tokens = None
            logger.warning("模型 Token 总数与输入输出之和不一致，摘要总数保持未知")
        duration, ttft, output_duration = self._timings()
        provider = next((value for value in (metadata.get("ls_provider"), metadata.get("upstream"))
                         if value in ("direct", "gateway")), self.provider)
        inputs = getattr(tree, "inputs", None)
        name = optional_identity(metadata.get("ls_model_name")) or (
            optional_identity(inputs.get("model")) if isinstance(inputs, dict) else None
        ) or self.model
        data = ExecutionData(
            trace_id=optional_identity(getattr(tree, "trace_id", None)),
            span_id=optional_identity(getattr(tree, "id", None)),
            parent_span_id=optional_identity(getattr(tree, "parent_run_id", None)),
            # 服务只执行模型请求，这两个字段按当前业务合同固定，不识别工具执行。
            span_kind="model", run_name="model-inference",
            started_at=trace_time(tree, metadata, "start_time", self.started_at),
            ended_at=trace_time(tree, metadata, "end_time", self.ended_at),
            duration_ms=trace_duration(tree, metadata, duration), status=status,
            error=self._error(metadata, status, error),
        )
        data.model = ModelUsage(
            provider=provider, name=name, input_tokens=input_tokens, output_tokens=output_tokens,
            total_tokens=total_tokens, usage_source=None, usage_status=None,
            ttft_ms=trace_metric(metadata, "ttft_ms", ttft) if self.streaming else None,
            output_duration_ms=trace_metric(metadata, "output_duration_ms", output_duration) if self.streaming else None,
        )
        record = ExecutionRecord(identity=self.identity, data=data)
        return record.model_dump(mode="json", exclude_unset=True)


def observe_safely(observation: ModelObservation | None, method: str, *args: Any) -> None:
    """隔离观测组件故障，统计失败不得改变网络请求及正式模型响应。"""
    if observation is not None:
        try:
            getattr(observation, method)(*args)
        except Exception:
            logger.warning("模型调用观测失败，推理继续")
