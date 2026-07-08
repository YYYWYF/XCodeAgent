import httpx
from langchain_openai import ChatOpenAI

from app.config import Settings


def create_chat_model(settings: Settings) -> ChatOpenAI:
    """Create an OpenAI API-compatible chat model for Deep Agents."""

    if not settings.anthropic_auth_token:
        raise RuntimeError(
            "Missing model API key. Set app_API_KEY, or OPENAI_API_KEY for compatibility."
        )

    return ChatOpenAI(
        model=settings.model_api_name,
        base_url=settings.anthropic_base_url,
        api_key=settings.anthropic_auth_token,
        temperature=settings.default_temperature,
        http_client=httpx.Client(trust_env=settings.anthropic_trust_env),
        http_async_client=httpx.AsyncClient(trust_env=settings.anthropic_trust_env),
    )
