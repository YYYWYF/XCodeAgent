"""只识别公开的真实模型增量，保存单次调用时钟而不持有正文。"""

from dataclasses import dataclass
from time import monotonic_ns

from langchain_core.messages import BaseMessageChunk
from langchain_core.outputs import ChatGenerationChunk

SYNTHETIC_TIMING_KEY = "_ttft_synthetic"


def _text(value):
    """允许真实空白增量，拒绝非字符串或空文本。"""
    return isinstance(value, str) and bool(value)


def _tool(value):
    """仅工具名称和原始参数字符串算增量，ID 和空参数对象不算。"""
    return isinstance(value, dict) and any(_text(value.get(key)) for key in ("name", "args", "arguments"))


def _content(value):
    """按 SDK 明确的文本、推理和工具分块结构判断，忽略未知元数据。"""
    if isinstance(value, str):
        return bool(value)
    if not isinstance(value, list):
        return False
    for block in value:
        if _text(block):
            return True
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind in {"text", "text_delta", "output_text", "summary_text"} and _text(block.get("text")):
            return True
        if kind in {"thinking", "thinking_delta", "reasoning"}:
            if any(_text(block.get(key)) for key in ("thinking", "reasoning", "text")):
                return True
            if _content(block.get("summary")):
                return True
        if kind in {"tool_call_chunk", "function_call", "tool_use"} and _tool(block):
            return True
        if kind == "input_json_delta" and _text(block.get("partial_json")):
            return True
    return False


def has_effective_model_delta(token, chunk=None):
    """只消费增量回调；明确排除非流式消息及带标记的最终合成 Chunk。"""
    if chunk is not None:
        if not isinstance(chunk, ChatGenerationChunk):
            return False
        if (chunk.generation_info or {}).get(SYNTHETIC_TIMING_KEY):
            return False
        message = chunk.message
        if not isinstance(message, BaseMessageChunk):
            return False
        if _content(message.content) or any(_tool(item) for item in getattr(message, "tool_call_chunks", [])):
            return True
        additional = message.additional_kwargs
        if any(_text(additional.get(key)) for key in ("reasoning_content", "thinking", "reasoning")):
            return True
        if _tool(additional.get("function_call")):
            return True
        if any(_tool(item.get("function")) for item in additional.get("tool_calls", []) if isinstance(item, dict)):
            return True
    return _content(token)


@dataclass
class ModelStreamTiming:
    """调用方在已有锁内维护首增量时间，不共享全局状态。"""

    started_ns: int
    first_token_ns: int | None = None

    def observe(self, token, chunk=None, *, observed_ns=None):
        """入口取时，首次有效增量后不再检查正文。"""
        observed_ns = monotonic_ns() if observed_ns is None else observed_ns
        if self.first_token_ns is None and has_effective_model_delta(token, chunk):
            self.first_token_ns = observed_ns

    def finish(self, ended_ns: int, *, duration_ms: int, rounded=False):
        """总耗时和首耗时采用同一取整方式，后续耗时取差以保证加和。"""
        first = self.first_token_ns
        if first is None or not self.started_ns <= first <= ended_ns:
            return {"ttft_ms": None, "output_duration_ms": None}
        elapsed = first - self.started_ns
        ttft = round(elapsed / 1_000_000) if rounded else elapsed // 1_000_000
        return {"ttft_ms": ttft, "output_duration_ms": duration_ms - ttft}
