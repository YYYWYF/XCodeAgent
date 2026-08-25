"""模型传输层错误的有界重试。

OpenAI/Anthropic SDK 的 max_retries 只覆盖请求建立阶段（连接失败、超时、5xx、429）。
流式读取中途断连（httpx.RemoteProtocolError: incomplete chunked read 等）发生在响应建立
之后，SDK 不会重试，异常直接穿透到 Graph 节点导致整个 workflow 失败。模型服务与中间网关
在长流式输出下偶发断连属于可恢复的传输抖动，这里在调用层做有界重试兜底。

内容类错误（ValueError：模型未返回合法 JSON、校验不一致等）不在此重试，
由各自的业务校验重试循环处理。
"""

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger("uvicorn.error")

# SDK 可选导入：缺省环境下也能引用本模块（如单测只测 httpx 分支）。
try:  # pragma: no cover - 导入分支由环境决定
    from openai import APIConnectionError, APITimeoutError

    _OPENAI_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
        APIConnectionError,
        APITimeoutError,
    )
except ImportError:  # pragma: no cover
    _OPENAI_TRANSPORT_ERRORS = ()

# 流式读取中途的传输层错误：对端断连、读取失败、连接重置、各类超时。
TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    httpx.WriteError,
    *_OPENAI_TRANSPORT_ERRORS,
)

DEFAULT_TRANSPORT_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (1.0, 2.0)


def run_with_transport_retry(
    operation: Callable[[], Any],
    *,
    attempts: int = DEFAULT_TRANSPORT_ATTEMPTS,
    operation_name: str = "模型调用",
) -> Any:
    """在传输层错误上有界重试模型调用；其余异常原样抛出。

    operation 每次调用必须是全新的一次模型请求（重建流式迭代器），
    不能在已中断的流上续读。
    """

    last_error: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return operation()
        except TRANSPORT_ERRORS as exc:
            last_error = exc
            if attempt >= max(1, attempts):
                break
            backoff = _RETRY_BACKOFF_SECONDS[min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)]
            logger.warning(
                "model_transport_retry: %s 第 %s/%s 次遇到传输错误，%ss 后重试：%s",
                operation_name,
                attempt,
                max(1, attempts),
                backoff,
                exc,
            )
            time.sleep(backoff)
    assert last_error is not None
    raise last_error
