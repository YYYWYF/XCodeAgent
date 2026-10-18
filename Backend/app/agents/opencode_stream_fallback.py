"""仅处理 OpenCode 流式入口拒绝新会话的已验证故障。"""

from typing import Any
from urllib.parse import urlparse

import anthropic
import openai


def nonstream_fallback(model: Any, error: Exception) -> Any | None:
    """无输出的明确路由拒绝只补发一次非流式请求，保留会话、模型和所有业务参数。"""

    base = str(getattr(model, "openai_api_base", None) or getattr(model, "anthropic_api_url", ""))
    if urlparse(base).hostname != "opencode.ai" or not model.streaming or model.disable_streaming:
        return None
    if not isinstance(error, (openai.APIStatusError, anthropic.APIStatusError)) or error.status_code != 400:
        return None
    body = error.body
    detail = body.get("error", body) if isinstance(body, dict) else {}
    if not isinstance(detail, dict) or "[model_coming_soon]" not in str(detail.get("message", "")):
        return None
    # 浅拷贝共享 HTTP 连接，调用级关闭流式，不修改缓存模型或其他并发会话。
    # disable_streaming 同时保证公开 stream/astream 走 SDK 原生的单消息流适配。
    return model.model_copy(update={"streaming": False, "disable_streaming": True})
