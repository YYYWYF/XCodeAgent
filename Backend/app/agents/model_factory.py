from typing import Any

import httpx
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.services.model_output_logger import ModelOutputLogHandler


def create_chat_model(
    settings: Settings,
    *,
    max_tokens_override: int | None = None,
    max_retries_override: int | None = None,
    timeout_seconds_override: float | None = None,
    extra_model_kwargs: dict | None = None,
) -> ChatOpenAI | ChatAnthropic:
    """根据 provider 和调用级覆盖创建聊天模型，不回写全局 Settings。

    支持 openai（OpenAI 兼容协议）与 anthropic（Anthropic 原生协议）两种 provider。
    override 为 None 时沿用全局值；max_retries_override=0 显式关闭 SDK 重试。
    OpenAI 兼容协议的 timeout 同步作用于模型与两个 HTTP 客户端，连接超时仍
    保持原有的 30 秒。

    extra_model_kwargs 可传入额外的模型参数，通过 extra_body 包裹后随 HTTP 请求体
    发送。用于 UI 设计稿生成等场景关闭推理模型的 thinking（如 GLM-5.2 的
    {"thinking": {"type": "disabled"}, "reasoning_effort": "none"}）。
    model_kwargs 会被 SDK 解包为 create() 方法的 keyword arguments，非标准
    参数会 TypeError；extra_body 是 Anthropic 和 OpenAI SDK 都支持的标准参数，
    其内容会被合并进 HTTP 请求体，不被 SDK 方法签名校验。网关不认识时被忽略。
    """

    if not settings.model_api_key:
        raise RuntimeError(
            "Missing model API key. Set MODEL_API_KEY, or OPENAI_API_KEY for compatibility."
        )

    # 不能用 `override or default`，因为 0 是关闭 SDK 重试的合法配置。
    max_tokens = (
        settings.default_max_tokens
        if max_tokens_override is None
        else max_tokens_override
    )
    max_retries = (
        settings.model_max_retries
        if max_retries_override is None
        else max_retries_override
    )
    timeout_seconds = (
        settings.model_timeout_seconds
        if timeout_seconds_override is None
        else timeout_seconds_override
    )
    timeout = httpx.Timeout(
        timeout=timeout_seconds,
        connect=30.0,
    )

    # 非标准参数（如 thinking/reasoning_effort）通过 extra_body 传递：
    # extra_body 的内容会直接合并进 HTTP 请求体，不被 SDK 方法签名校验，
    # 网关透传给 GLM 可关闭思考。ChatOpenAI/ChatAnthropic 都有顶层
    # extra_body 字段，直接传给构造函数；嵌在 model_kwargs 里会触发
    # UserWarning（Parameters {'extra_body'} should be specified explicitly）。
    extra_body = dict(extra_model_kwargs) if extra_model_kwargs else None

    if settings.model_provider == "anthropic":
        # Anthropic 原生协议：走 /v1/messages，使用 x-api-key + anthropic-version 鉴权
        # 注意 ChatAnthropic 的字段名与 ChatOpenAI 不同：anthropic_api_url / anthropic_api_key
        # 显式传 max_tokens：ChatAnthropic 默认 None 会让 Anthropic SDK 退回 1024，
        # 对需要输出代码 + 最终 task_results JSON 的 Deep Agent 远远不够，会被截断。
        # ChatAnthropic 有原生 thinking 字段，直接设置才能进入请求体 payload；
        # 通过 extra_body 传递时不会设置原生字段，thinking 不会被关闭。
        anthropic_extra: dict[str, Any] = {}
        if extra_model_kwargs and "thinking" in extra_model_kwargs:
            anthropic_extra["thinking"] = extra_model_kwargs["thinking"]
        headers = {"anthropic-version": settings.anthropic_api_version}
        headers.update(settings.model_custom_headers)
        return ChatAnthropic(
            model=settings.model_api_name,
            anthropic_api_url=settings.model_base_url,
            anthropic_api_key=settings.model_api_key,
            default_headers=headers,
            temperature=settings.default_temperature,
            max_tokens=max_tokens,
            default_request_timeout=timeout_seconds,
            max_retries=max_retries,
            # streaming 必须始终为 True：requirements_analyzer / planner /
            # product_planner 等用 runnable.stream() 迭代 AIMessageChunk；
            # streaming=False 时返回完整 AIMessage，isinstance(chunk, AIMessageChunk)
            # 不匹配，chunk 被丢弃，最终返回空 messages 误报"未返回完整 JSON"。
            # model_output_log_enabled 只控制是否打印日志，不再复用为 streaming 开关。
            streaming=True,
            callbacks=(
                [ModelOutputLogHandler()]
                if settings.model_output_log_enabled
                else None
            ),
            **anthropic_extra,
        )

    # OpenAI 兼容协议：走 /v1/chat/completions
    return ChatOpenAI(
        model=settings.model_api_name,
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
        temperature=settings.default_temperature,
        max_tokens=max_tokens,
        timeout=timeout_seconds,
        max_retries=max_retries,
        http_client=httpx.Client(
            trust_env=settings.model_trust_env,
            timeout=timeout,
        ),
        http_async_client=httpx.AsyncClient(
            trust_env=settings.model_trust_env,
            timeout=timeout,
        ),
        streaming=True,
        callbacks=(
            [ModelOutputLogHandler()] if settings.model_output_log_enabled else None
        ),
        extra_body=extra_body,
    )
