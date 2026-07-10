from __future__ import annotations

from typing import Any


def last_agent_text(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    if not messages:
        return "Agent completed without a text message."
    content = getattr(messages[-1], "content", "")
    return content if isinstance(content, str) else str(content)
