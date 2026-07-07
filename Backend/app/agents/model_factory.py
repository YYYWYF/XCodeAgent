from langchain_openai import ChatOpenAI

from app.config import Settings


def create_chat_model(settings: Settings) -> ChatOpenAI:
    """Create an OpenAI API-compatible chat model for Deep Agents."""

    if not settings.anthropic_auth_token:
        raise RuntimeError(
            "Missing model API key. Set app_API_KEY, or OPENAI_API_KEY for compatibility."
        )

    return ChatOpenAI(
        model=settings.anthropic_model,
        base_url=settings.anthropic_base_url,
        api_key=settings.anthropic_auth_token,
        temperature=settings.default_temperature,
    )
