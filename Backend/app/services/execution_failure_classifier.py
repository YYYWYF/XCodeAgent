"""把运行时异常转换为脱敏的 Durable Failure Evidence。"""

from __future__ import annotations

from typing import Any

from app.domain.execution_recovery import (
    ExecutionFailureEvidence,
    ExecutionFailureOrigin,
)


_MODEL_OPERATIONS = frozenset(
    {
        "requirements",
        "product_planning",
        "ui_confirmation",
        "technical_planning_generate",
        "technical_planning",
    }
)
_NETWORK_ERROR_NAMES = frozenset(
    {
        "ConnectError",
        "ConnectTimeout",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "WriteError",
        "TimeoutError",
        "ConnectionError",
    }
)


def classify_execution_failure(
    exc: BaseException,
    *,
    operation: str | None = None,
) -> ExecutionFailureEvidence:
    """根据异常类型和受控操作上下文生成不含原始消息的失败证据。"""

    normalized_operation = str(operation or "").strip() or None
    status = _http_status(exc)
    module_name = type(exc).__module__.lower()
    class_name = type(exc).__name__
    is_model_exception = (
        module_name.startswith("openai")
        or module_name.startswith("anthropic")
        or "model" in module_name
    )
    is_network_exception = class_name in _NETWORK_ERROR_NAMES or module_name.startswith(
        "httpx"
    )
    is_model_operation = normalized_operation in _MODEL_OPERATIONS

    if (is_model_exception or (is_model_operation and is_network_exception)):
        return ExecutionFailureEvidence(
            origin=ExecutionFailureOrigin.MODEL_CALL,
            code=_stable_code(exc, status),
            operation=normalized_operation,
            dependency="model",
            provider=_provider_from_exception(exc, module_name),
            model=_model_from_exception(exc),
            http_status=status,
            replay_compatible=True,
        )
    if is_network_exception:
        return ExecutionFailureEvidence(
            origin=ExecutionFailureOrigin.EXTERNAL_DEPENDENCY,
            code=_stable_code(exc, status),
            operation=normalized_operation,
            dependency="external",
            http_status=status,
            replay_compatible=True,
        )
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        origin = ExecutionFailureOrigin.BUSINESS
    else:
        origin = ExecutionFailureOrigin.UNKNOWN
    return ExecutionFailureEvidence(
        origin=origin,
        code=_stable_code(exc, status),
        operation=normalized_operation,
        http_status=status,
        replay_compatible=False,
    )


def _http_status(exc: BaseException) -> int | None:
    """只读取异常对象上的整数 HTTP 状态，不持久化 response 内容。"""

    response = getattr(exc, "response", None)
    value = getattr(exc, "status_code", None)
    if value is None and response is not None:
        value = getattr(response, "status_code", None)
    try:
        parsed = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and 100 <= parsed <= 599 else None


def _stable_code(exc: BaseException, status: int | None) -> str:
    """生成稳定错误码，避免把敏感或可变的异常文本写入恢复库。"""

    class_name = type(exc).__name__.strip().lower().replace(" ", "_")
    return f"http_{status}" if status is not None else class_name[:256] or "unknown_error"


def _provider_from_exception(exc: BaseException, module_name: str) -> str | None:
    """从异常模块或安全 provider 属性提取诊断用 provider 名称。"""

    provider = getattr(exc, "provider", None)
    if isinstance(provider, str) and provider.strip():
        return provider.strip()[:128]
    if module_name.startswith("openai"):
        return "openai"
    if module_name.startswith("anthropic"):
        return "anthropic"
    return None


def _model_from_exception(exc: BaseException) -> str | None:
    """仅读取异常对象明确暴露的 model 字段，不解析原始消息。"""

    model = getattr(exc, "model", None)
    return model.strip()[:256] if isinstance(model, str) and model.strip() else None


__all__ = ["classify_execution_failure"]
