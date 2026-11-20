"""消费 LangChain 真实用量和单测首轮输入大小，不保存消息正文。"""

from collections import OrderedDict
from threading import Lock

from langchain_core.callbacks import BaseCallbackHandler

from app.observability.build_instrumentation import finish, record_prompt, start_model, _exception_status, _is_cancelled


def _usage(response, provider, name, stream_usage=None):
    """选用同一份最终 Usage 快照，不叠加重复尾帧或估算缺失字段。"""
    last = None
    output = getattr(response, "llm_output", None) or {}
    for group in getattr(response, "generations", []) or []:
        for generation in group:
            message = getattr(generation, "message", None)
            metadata = getattr(message, "response_metadata", None) or {}
            name = metadata.get("model_name") or metadata.get("model") or name
            provider = metadata.get("model_provider") or provider
            usage = metadata.get("token_usage") or getattr(message, "usage_metadata", None)
            if usage:
                last = usage
    last = stream_usage or last or output.get("token_usage") or {}
    name = output.get("model_name") or name
    values = {}
    for key, provider_key in (("input_tokens", "prompt_tokens"),
                              ("output_tokens", "completion_tokens"),
                              ("total_tokens", "total_tokens")):
        count = last.get(key, last.get(provider_key))
        values[key] = count if type(count) is int and count >= 0 else None
    present = sum(value is not None for value in values.values())
    return {**values, "provider": provider, "name": name,
            "usage_source": "provider" if present else "unavailable",
            "usage_status": "complete" if present == 3 else "partial" if present else "unavailable"}


class BuildModelUsageCallback(BaseCallbackHandler):
    """同一个 LangChain run_id 恰好产生一次终态片段。"""

    run_inline = True

    def __init__(self, provider, name):
        """保存直连模型标识，并为可能中断的调用保留有限的在途表。"""
        self.provider = provider
        self.name = name
        self.inflight = OrderedDict()
        self.lock = Lock()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        """入栈时捕获并发上下文，只统计单测首轮的实际消息长度。"""
        try:
            span = start_model()
            if span is None:
                return
            with self.lock:
                self.inflight[run_id] = (span, None)
                evicted = self.inflight.popitem(last=False)[1] if len(self.inflight) > 1024 else None
            if evicted is not None:
                finish(evicted[0], "interrupted", model=_usage(None, self.provider, self.name, evicted[1]))
            if span.trace.business_stage == "unit_test" and span.extra.get("model_round") == 1 and messages:
                first_request = messages[0]
                record_prompt(
                    "\n".join(message.text for message in first_request),
                    task_context="\n".join(message.text for message in first_request if message.type == "human"),
                )
        except Exception:
            pass

    def on_llm_new_token(self, token, *, chunk=None, run_id, **kwargs):
        """对累计 Usage 尾帧取最新快照，Anthropic 的分段用量沿用 SDK 聚合。"""
        if self.provider == "anthropic" or chunk is None:
            return
        try:
            message = getattr(chunk, "message", None)
            meta = getattr(message, "response_metadata", None) or {}
            usage = meta.get("token_usage") or getattr(message, "usage_metadata", None)
            if not isinstance(usage, dict) or not usage:
                return
            with self.lock:
                current = self.inflight.get(run_id)
                if current is not None:
                    # 仅读取标准 Token 数字，不保存供应商其他元数据。
                    keys = ("input_tokens", "prompt_tokens", "output_tokens", "completion_tokens", "total_tokens")
                    snapshot = {key: usage[key] for key in keys if type(usage.get(key)) is int}
                    if snapshot:
                        self.inflight[run_id] = (current[0], snapshot)
        except Exception:
            pass

    def on_llm_end(self, response, *, run_id, **kwargs):
        """正常结束时仅读取 LangChain 已合并的终态消息。"""
        self._finish(run_id, response, "completed")

    def on_llm_error(self, error, *, run_id, **kwargs):
        """取消或失败时仍使用已有的部分响应，缺失用量保留 null。"""
        self._finish(run_id, kwargs.get("response"), _exception_status(error))

    def _finish(self, run_id, response, status):
        """原子取出状态以避免重复回调被再次累计。"""
        try:
            with self.lock:
                current = self.inflight.pop(run_id, None)
            if current is None:
                return
            span, stream_usage = current
            try:
                usage = _usage(response, self.provider, self.name, stream_usage)
            except Exception:
                usage = _usage(None, self.provider, self.name)
            finish(span, "cancelled" if status != "completed" and _is_cancelled(span) else status, model=usage)
        except Exception:
            pass
