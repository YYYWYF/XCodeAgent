from __future__ import annotations

import json
import logging
from dataclasses import replace
from hashlib import sha256
from typing import Any, Callable

from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings


logger = logging.getLogger("uvicorn.error")


def invoke_technical_plan_model(
    prompt: str,
    *,
    settings: Settings,
    on_token: Callable[[str], None] | None = None,
) -> str:
    """使用独立输出预算调用技术规划模型，并保留结束原因和用量用于完整性检查。"""

    active_settings = replace(
        settings, default_max_tokens=settings.technical_plan_max_tokens
    )
    model = create_chat_model(
        active_settings,
        extra_model_kwargs={"thinking": {"type": "disabled"}},
    )
    message: AIMessage | AIMessageChunk | None = None
    if on_token is None:
        message = model.invoke(prompt)
    else:
        merged_chunk: AIMessageChunk | None = None
        for chunk in model.stream(prompt):
            if isinstance(chunk, AIMessageChunk):
                # 结束原因与 usage 通常位于空正文的最后一个 chunk，不能按正文过滤。
                merged_chunk = chunk if merged_chunk is None else merged_chunk + chunk
                message = merged_chunk
            elif isinstance(chunk, AIMessage):
                # LangChain 可通过单个完整 AIMessage 实现 stream，保留其完整元数据。
                message = chunk
            else:
                continue
            token = _coerce_content_text(chunk.content)
            if token:
                on_token(token)
    text = _coerce_content_text(message.content) if message is not None else ""
    _validate_completion(message, text, active_settings.default_max_tokens)
    return text


def _validate_completion(
    message: AIMessage | AIMessageChunk | None,
    text: str,
    configured_max_tokens: int,
) -> None:
    """记录有界脱敏诊断，并在模型声明截断或非正常结束时拒绝产物。"""

    metadata = message.response_metadata if message is not None else {}
    raw_reason = metadata.get("finish_reason")
    known_reasons = {"stop", "length", "content_filter", "tool_calls", "function_call"}
    reason = (
        raw_reason
        if isinstance(raw_reason, str) and raw_reason in known_reasons
        else "unknown" if raw_reason is not None else None
    )
    usage = message.usage_metadata if message is not None else None
    if not isinstance(usage, dict):
        usage = metadata.get("token_usage")
    usage = usage if isinstance(usage, dict) else {}
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    logger.info(
        "technical_plan_model_response response_chars=%s response_sha256=%s "
        "finish_reason=%s output_tokens=%s configured_max_tokens=%s",
        len(text),
        sha256(text.encode("utf-8")).hexdigest()[:16],
        reason,
        output_tokens if type(output_tokens) is int else None,
        configured_max_tokens,
    )
    if reason == "length":
        raise ValueError(
            "TechnicalPlan 模型输出达到输出上限而被截断"
            f"（finish_reason=length，max_tokens={configured_max_tokens}），"
            "不能作为完整 JSON 进入契约校验。"
        )
    if reason not in {None, "stop"}:
        raise ValueError(
            f"TechnicalPlan 模型输出未正常结束（finish_reason={reason}），不能生成正式产物。"
        )


def parse_technical_plan_json(text: str) -> dict[str, Any]:
    """只解析完整且唯一的根对象，拒绝从截断技术规划中提取嵌套对象。"""

    source = text.strip()
    if source.startswith("```"):
        lines = source.splitlines()
        if len(lines) < 3 or lines[0] not in {"```", "```json"} or lines[-1] != "```":
            raise ValueError("TechnicalPlan 模型输出必须是完整的 JSON 对象或单个 JSON 代码围栏。")
        source = "\n".join(lines[1:-1]).strip()
    try:
        result = json.loads(source)
    except json.JSONDecodeError as exc:
        # 错误只保留位置，不泄露模型正文，也不继续搜索内部可解析片段。
        raise ValueError(
            f"TechnicalPlan 模型输出 JSON 不完整或格式错误（位置 {exc.pos}），"
            "必须重新生成完整且唯一的根对象。"
        ) from None
    if not isinstance(result, dict):
        raise ValueError("TechnicalPlan 模型输出必须是完整且唯一的 JSON 对象。")
    return result
