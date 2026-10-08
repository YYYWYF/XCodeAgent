from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings


def is_qwen_screenshot_model(settings: Settings) -> bool:
    """判断当前截图模型是否为千问系列，以应用其多模态兼容参数。"""

    return settings.screenshot_model_api_name.lower().startswith("qwen")


def screenshot_response_format(settings: Settings) -> str:
    """选择截图结构化输出模式；千问多模态使用官方支持的 JSON Object。"""

    configured = settings.screenshot_response_format
    if configured not in {"auto", "json_schema", "json_object"}:
        raise ValueError(
            "XCODEAGENT_SCREENSHOT_RESPONSE_FORMAT 仅支持 auto、json_schema 或 json_object"
        )
    if configured != "auto":
        return configured
    if is_qwen_screenshot_model(settings):
        return "json_object"
    if "deepseek.com" in settings.screenshot_model_base_url.lower():
        return "json_object"
    return "json_schema"


def screenshot_request_options(settings: Settings) -> dict[str, Any]:
    """返回模型厂商扩展参数；千问关闭思考以稳定 JSON 并缩短视觉响应时间。"""

    return {"enable_thinking": False} if is_qwen_screenshot_model(settings) else {}


def screenshot_http_timeout(settings: Settings) -> httpx.Timeout:
    """构造独立于普通对话的截图模型超时，适配多图高分辨率请求。"""

    return httpx.Timeout(timeout=settings.screenshot_timeout_seconds, connect=30.0)
