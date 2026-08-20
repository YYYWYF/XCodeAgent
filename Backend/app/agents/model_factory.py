import httpx
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.services.model_output_logger import ModelOutputLogHandler


def create_chat_model(
    settings: Settings,
    *,
    extra_model_kwargs: dict | None = None,
) -> ChatOpenAI:
    """Create an OpenAI API-compatible chat model for Deep Agents.

    extra_model_kwargs 可传入额外的模型参数，通过 extra_body 包裹后随 HTTP 请求体
    发送。用于 UI 设计稿生成等场景关闭推理模型的 thinking（如 GLM-5.2 的
    {"thinking": {"type": "disabled"}, "reasoning_effort": "none"}）。
    model_kwargs 会被 SDK 解包为 create() 方法的 keyword arguments，非标准
    参数会 TypeError；extra_body 是标准参数，其内容会被合并进 HTTP 请求体，
    不被 SDK 方法签名校验。网关不认识时被忽略。
    """

    if not settings.model_api_key:
        raise RuntimeError(
            "Missing model API key. Set MODEL_API_KEY, or OPENAI_API_KEY for compatibility."
        )

    timeout = httpx.Timeout(
        timeout=settings.model_timeout_seconds,
        connect=30.0,
    )

    # 非标准参数（如 thinking/reasoning_effort）必须包裹在 extra_body 里：
    # model_kwargs 会被解包为 SDK create() 方法的 keyword arguments，
    # 非标准参数会导致 TypeError；extra_body 的内容会直接合并进 HTTP
    # 请求体，不被 SDK 方法签名校验。网关透传给 GLM 可关闭思考。
    model_kwargs = (
        {"extra_body": dict(extra_model_kwargs)} if extra_model_kwargs else {}
    )

    # OpenAI 兼容协议：走 /v1/chat/completions
    return ChatOpenAI(
        model=settings.model_api_name,
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
        temperature=settings.default_temperature,
        max_tokens=settings.default_max_tokens,
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
        model_kwargs=model_kwargs,
    )
