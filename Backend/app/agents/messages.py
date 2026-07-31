from __future__ import annotations

from typing import Any


NO_AGENT_TEXT = "Agent completed without a text message."


def optional_last_agent_text(result: dict[str, Any]) -> str | None:
    """读取最后一条 Agent 文本；结果不含消息时返回空值供流式回退处理。"""

    messages = result.get("messages", [])
    if not messages:
        return None
    content = getattr(messages[-1], "content", "")
    text = _coerce_content_text(content)
    return text or None


def last_agent_text(result: dict[str, Any]) -> str:
    """读取最后一条 Agent 文本，并为无文本结果提供稳定占位说明。"""

    return optional_last_agent_text(result) or NO_AGENT_TEXT


def _coerce_content_text(content: Any) -> str:
    """把模型消息 content 规整为纯文本，正确处理 Anthropic 的 content block 列表。

    content 可能是纯字符串，也可能是 content block 列表（如
    ``[{"type": "text", "text": "..."}]``）。后者若直接 ``str()`` 会得到
    Python repr，破坏其中嵌入的 JSON，导致下游 ``extract_json_object`` 解析失败、
    被派发任务被误判为 omitted。这里按 block 顺序拼接 text 字段，保留原始文本。
    """

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                block_text = block.get("text")
                if isinstance(block_text, str):
                    parts.append(block_text)
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content) if content else ""
