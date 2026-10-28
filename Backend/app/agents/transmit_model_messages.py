"""复用现有 OpenAI 消息转换器，完整保留转发结果与工具参数分片。"""

from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_openai.chat_models.base import (
    _convert_delta_to_message_chunk,
    _convert_dict_to_message,
    _create_usage_metadata,
)

from app.services.model_transmit_client import ModelTransmitError


def completion_result(value: dict[str, Any]) -> ChatResult:
    """将完整 Chat Completions 结果转换为 LangChain 正式消息。"""
    generations = []
    for choice in value.get("choices", []):
        raw_message = choice.get("message")
        if not isinstance(raw_message, dict):
            raise ModelTransmitError("模型完整结果缺少 message", code="INVALID_MODEL_RESULT")
        message = _convert_dict_to_message(raw_message)
        if not isinstance(message, AIMessage):
            raise ModelTransmitError("模型返回了非 assistant 消息", code="INVALID_MODEL_RESULT")
        if raw_message.get("tool_calls"):
            message.additional_kwargs["tool_calls"] = raw_message["tool_calls"]
        info = {"finish_reason": choice.get("finish_reason"), "model_name": value.get("model")}
        for key in ("logprobs",):
            if key in choice:
                info[key] = choice[key]
        message.response_metadata = dict(info)
        if "usage" in value and value["usage"] is not None:
            message.usage_metadata = _create_usage_metadata(value["usage"])
            message.response_metadata["token_usage"] = value["usage"]
        generations.append(ChatGeneration(message=message, generation_info=info))
    if not generations:
        raise ModelTransmitError("模型完整结果没有候选消息", code="INVALID_MODEL_RESULT")
    return ChatResult(generations=generations, llm_output={
        "token_usage": value.get("usage", {}), "model_name": value.get("model"),
        "system_fingerprint": value.get("system_fingerprint"),
    })


def generation_chunk(value: dict[str, Any]) -> ChatGenerationChunk | None:
    """保留首候选的工具 JSON 原始分片、结束原因和上游用量。"""
    choices = value.get("choices", [])
    # 多候选上游可能把 index=1 单独放在一帧；只投影首候选，不能按帧内位置混合。
    choice = next((item for item in choices if item.get("index", 0) == 0), {})
    if not choice and value.get("usage") is None:
        return None
    delta = choice.get("delta", {})
    message = _convert_delta_to_message_chunk(delta, AIMessageChunk)
    if not isinstance(message, AIMessageChunk):
        raise ModelTransmitError("模型返回了非 assistant 增量", code="INVALID_MODEL_RESULT")
    info = {}
    if choice.get("finish_reason") is not None:
        info["finish_reason"] = choice["finish_reason"]
    if choice.get("logprobs") is not None:
        info["logprobs"] = choice["logprobs"]
    if value.get("model") and choice.get("finish_reason") is not None:
        message.response_metadata["model_name"] = value["model"]
    if value.get("usage") is not None:
        message.usage_metadata = _create_usage_metadata(value["usage"])
        message.response_metadata["token_usage"] = value["usage"]
    return ChatGenerationChunk(message=message, generation_info=info or None)


def final_stream_chunk(value: dict[str, Any], *, had_chunks: bool,
                       had_usage: bool, had_finish: bool = False) -> ChatGenerationChunk | None:
    """终态完整结果仅补充未收到的内容或用量，避免与已输出增量重复。"""
    if not had_chunks:
        result = completion_result(value)
        message = result.generations[0].message
        chunk = AIMessageChunk(content=message.content, additional_kwargs=message.additional_kwargs,
                               tool_calls=message.tool_calls, invalid_tool_calls=message.invalid_tool_calls,
                               response_metadata=message.response_metadata, usage_metadata=message.usage_metadata)
        return ChatGenerationChunk(message=chunk, generation_info=result.generations[0].generation_info)
    choice = (value.get("choices") or [{}])[0]
    info = {"finish_reason": choice["finish_reason"], "model_name": value.get("model")} \
        if not had_finish and choice.get("finish_reason") is not None else {}
    message = AIMessageChunk(content="")
    if not had_usage and value.get("usage") is not None:
        message.usage_metadata = _create_usage_metadata(value["usage"])
        message.response_metadata["token_usage"] = value["usage"]
    if not info and message.usage_metadata is None:
        return None
    return ChatGenerationChunk(message=message, generation_info=info or None)
