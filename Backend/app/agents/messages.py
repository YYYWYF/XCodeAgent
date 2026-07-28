from __future__ import annotations

from typing import Any


NO_AGENT_TEXT = "Agent completed without a text message."


def optional_last_agent_text(result: dict[str, Any]) -> str | None:
    """读取最后一条 Agent 文本；结果不含消息时返回空值供流式回退处理。"""

    messages = result.get("messages", [])
    if not messages:
        return None
    content = getattr(messages[-1], "content", "")
    text = content if isinstance(content, str) else str(content)
    return text or None


def last_agent_text(result: dict[str, Any]) -> str:
    """读取最后一条 Agent 文本，并为无文本结果提供稳定占位说明。"""

    return optional_last_agent_text(result) or NO_AGENT_TEXT
