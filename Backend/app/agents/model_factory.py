"""所有 Agent 共用的模型转发入口，不在 Studio 持有上游地址或密钥。"""

from copy import deepcopy

from app.agents.transmit_chat_model import TransmitChatModel
from app.config import Settings
from app.services.model_output_logger import ModelOutputLogHandler


def create_chat_model(
    settings: Settings,
    *,
    max_tokens_override: int | None = None,
    max_retries_override: int | None = None,
    timeout_seconds_override: float | None = None,
    extra_model_kwargs: dict | None = None,
) -> TransmitChatModel:
    """创建转发模型并保留调用预算覆盖；零重试不会被全局默认值覆盖。"""
    return TransmitChatModel(
        transmit_url=settings.model_transmit_url,
        transmit_token=settings.model_transmit_token,
        trust_env=settings.model_transmit_trust_env,
        spaid=settings.model_transmit_spaid,
        temperature=settings.default_temperature,
        max_tokens=settings.default_max_tokens if max_tokens_override is None else max_tokens_override,
        max_retries=settings.model_max_retries if max_retries_override is None else max_retries_override,
        timeout_seconds=settings.model_timeout_seconds if timeout_seconds_override is None else timeout_seconds_override,
        extra_body=deepcopy(extra_model_kwargs) if extra_model_kwargs else None,
        callbacks=[ModelOutputLogHandler()] if settings.model_output_log_enabled else None,
    )
