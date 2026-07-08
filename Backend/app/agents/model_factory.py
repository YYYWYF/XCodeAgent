import httpx
from langchain_openai import ChatOpenAI

from app.config import Settings


def create_chat_model(settings: Settings) -> ChatOpenAI:
    """Create an OpenAI API-compatible chat model for Deep Agents."""

    if not settings.model_api_key:
        raise RuntimeError(
            "Missing model API key. Set MODEL_API_KEY, or OPENAI_API_KEY for compatibility."
        )

    return ChatOpenAI(
        model=settings.model_api_name,
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
        temperature=settings.default_temperature,
        http_client=httpx.Client(trust_env=settings.model_trust_env),
        http_async_client=httpx.AsyncClient(trust_env=settings.model_trust_env),
    )
