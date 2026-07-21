import httpx
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.services.model_output_logger import ModelOutputLogHandler


def create_chat_model(settings: Settings) -> ChatOpenAI:
    """Create an OpenAI API-compatible chat model for Deep Agents."""

    if not settings.model_api_key:
        raise RuntimeError(
            "Missing model API key. Set MODEL_API_KEY, or OPENAI_API_KEY for compatibility."
        )

    timeout = httpx.Timeout(
        timeout=settings.model_timeout_seconds,
        connect=30.0,
    )

    return ChatOpenAI(
        model=settings.model_api_name,
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
        temperature=settings.default_temperature,
        timeout=settings.model_timeout_seconds,
        max_retries=settings.model_max_retries,
        http_client=httpx.Client(
            trust_env=settings.model_trust_env,
            timeout=timeout,
        ),
        http_async_client=httpx.AsyncClient(
            trust_env=settings.model_trust_env,
            timeout=timeout,
        ),
        streaming=settings.model_output_log_enabled,
        callbacks=(
            [ModelOutputLogHandler()] if settings.model_output_log_enabled else None
        ),
    )
