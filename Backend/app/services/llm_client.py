from __future__ import annotations

import httpx
from anthropic import AsyncAnthropic

from app.config import Settings


def create_anthropic_client(settings: Settings) -> AsyncAnthropic:
    return AsyncAnthropic(
        api_key=settings.anthropic_auth_token,
        base_url=settings.anthropic_base_url,
        http_client=httpx.AsyncClient(trust_env=settings.anthropic_trust_env),
    )
