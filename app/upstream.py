"""两种上游的纯传输适配；不依赖 Studio 业务类型。"""

import inspect
from collections.abc import AsyncIterator
from typing import Any

import httpx
from openai import AsyncOpenAI
from openai.resources.chat.completions import AsyncCompletions

from .config import Settings
from .contracts import InferenceInput

KNOWN_PARAMETERS = set(inspect.signature(AsyncCompletions.create).parameters) - {"self"}


def connection(settings: Settings, call: InferenceInput, thread_id: str, run_id: str) -> tuple[str, str, dict[str, str]]:
    """固定上游地址与凭证；只允许网关身份字段来自调用元数据。"""
    if settings.model_upstream == "direct":
        return settings.direct_base_url, settings.direct_api_key.get_secret_value(), {}
    metadata = call.metadata
    spaid = settings.gateway_spaid if settings.gateway_spaid_source == "fixed" else metadata.spaid
    if not spaid:
        raise ValueError("gateway 上游要求提供 metadata.spaid")
    profile = settings.gateway_profile
    return profile.base_url, settings.gateway_api_key.get_secret_value(), {
        "x-spaid": spaid,
        "x-session-id": metadata.session_id or thread_id,
        "x-request-id": metadata.request_id or run_id,
        "x-gateway-key": settings.gateway_key.get_secret_value(),
    }


class Upstream:
    """使用官方 OpenAI SDK 处理流与重试，不手动拆解 SSE。"""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        """保存服务端设置并允许测试注入离线 HTTP 传输。"""
        self.settings = settings
        self.transport = transport

    async def invoke(self, call: InferenceInput, thread_id: str, run_id: str) -> AsyncIterator[dict[str, Any]]:
        """在单次连接内生成原始响应，退出与取消时始终关闭上游。"""
        base_url, api_key, headers = connection(self.settings, call, thread_id, run_id)
        body = {**call.request, "model": self.settings.active_model}
        kwargs = {key: value for key, value in body.items() if key in KNOWN_PARAMETERS}
        extensions = {key: value for key, value in body.items() if key not in KNOWN_PARAMETERS}
        if extensions:
            kwargs["extra_body"] = extensions
        # 禁止跟随重定向并默认忽略系统代理，认证头只发往配置的固定地址。
        async with httpx.AsyncClient(transport=self.transport, trust_env=self.settings.upstream_trust_env, follow_redirects=False) as http_client:
            async with AsyncOpenAI(base_url=base_url, api_key=api_key, default_headers=headers,
                                   timeout=min(call.timeout_seconds, self.settings.max_timeout_seconds),
                                   max_retries=min(call.max_retries, self.settings.max_retries), http_client=http_client) as client:
                response = await client.chat.completions.create(**kwargs)
                if body.get("stream", False):
                    async with response:
                        async for chunk in response:
                            yield chunk.model_dump(mode="json", exclude_none=True)
                else:
                    yield response.model_dump(mode="json", exclude_none=True)
