"""Unit Candidate 模型调用边界的可信失败分类与公开文案。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import anthropic
import httpx
import openai

from app.domain.execution_recovery import ExecutionFailureEvidence, ExecutionFailureOrigin
from app.services.planning_issues import ValidationIssue


MODEL_SETUP_FAILED = "UNIT_GENERATION_MODEL_SETUP_FAILED"
MODEL_REQUEST_TIMEOUT = "UNIT_GENERATION_MODEL_REQUEST_TIMEOUT"
MODEL_CONNECTION_FAILED = "UNIT_GENERATION_MODEL_CONNECTION_FAILED"
MODEL_HTTP_ERROR = "UNIT_GENERATION_MODEL_HTTP_ERROR"
MODEL_RESPONSE_INVALID = "UNIT_GENERATION_MODEL_RESPONSE_INVALID"
MODEL_CALL_FAILED = "UNIT_GENERATION_MODEL_CALL_FAILED"
LEGACY_INFRASTRUCTURE_FAILURE = "UNIT_GENERATION_INFRASTRUCTURE_FAILURE"

MODEL_CALL_FAILURE_CODES = frozenset({
    MODEL_SETUP_FAILED, MODEL_REQUEST_TIMEOUT, MODEL_CONNECTION_FAILED,
    MODEL_HTTP_ERROR, MODEL_RESPONSE_INVALID, MODEL_CALL_FAILED,
})
_PROVIDER_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MESSAGES = {
    MODEL_SETUP_FAILED: "模型调用准备失败。",
    MODEL_REQUEST_TIMEOUT: "模型请求超时。",
    MODEL_CONNECTION_FAILED: "无法连接模型服务。",
    MODEL_HTTP_ERROR: "模型服务返回 HTTP 错误。",
    MODEL_RESPONSE_INVALID: "模型服务返回的响应无法读取。",
    MODEL_CALL_FAILED: "模型调用失败，未能确定具体原因。",
    LEGACY_INFRASTRUCTURE_FAILURE: "生成执行计划的本次处理已超时。",
}


@dataclass(frozen=True, slots=True)
class UnitModelFailure:
    """保存模型边界已确认的事实，不保存异常或响应对象。"""

    code: str
    stage: Literal["model_setup", "model_invoke", "unit_session"]
    http_status: int | None = None
    provider_error_code: str | None = None

    @property
    def message(self) -> str:
        """从固定文案表生成公开摘要，仅在确认 HTTP 响应时展示状态。"""

        if self.code == MODEL_HTTP_ERROR and self.http_status is not None:
            return f"模型服务返回 HTTP {self.http_status}。"
        return _MESSAGES[self.code]


def classify_unit_model_failure(
    cause: Exception,
    *,
    stage: Literal["model_setup", "model_invoke"],
) -> UnitModelFailure:
    """只在明确的模型准备或调用边界按实际 SDK 类型分类。"""

    if stage == "model_setup":
        return UnitModelFailure(MODEL_SETUP_FAILED, stage)
    if isinstance(cause, (openai.APITimeoutError, anthropic.APITimeoutError, httpx.TimeoutException)):
        return UnitModelFailure(MODEL_REQUEST_TIMEOUT, stage)
    if isinstance(cause, (openai.APIResponseValidationError, anthropic.APIResponseValidationError)):
        return UnitModelFailure(MODEL_RESPONSE_INVALID, stage, http_status=_status(cause))
    if isinstance(cause, (openai.APIStatusError, anthropic.APIStatusError)):
        return UnitModelFailure(
            MODEL_HTTP_ERROR, stage, http_status=_status(cause),
            provider_error_code=_provider_code(cause),
        )
    if isinstance(cause, (openai.APIConnectionError, anthropic.APIConnectionError, httpx.TransportError)):
        return UnitModelFailure(MODEL_CONNECTION_FAILED, stage)
    return UnitModelFailure(MODEL_CALL_FAILED, stage)


def unit_session_timeout_failure() -> UnitModelFailure:
    """整个 Unit Session 到期不冒充单次模型请求超时。"""

    return UnitModelFailure(LEGACY_INFRASTRUCTURE_FAILURE, "unit_session")


def is_unit_model_failure_issue(issue: ValidationIssue) -> bool:
    """仅允许旧码和明确新增的 Unit 模型码进入 Planning Recovery。"""

    return (
        issue.code in MODEL_CALL_FAILURE_CODES | {LEGACY_INFRASTRUCTURE_FAILURE}
        and issue.level == "system"
        and issue.category == "infrastructure"
        and not issue.retryable
    )


def failure_evidence_from_issue(issue: ValidationIssue) -> ExecutionFailureEvidence | None:
    """从已提交的主 Issue 构造公开证据，不解析异常文本。"""

    if not is_unit_model_failure_issue(issue):
        return None
    details = issue.details
    status = details.get("http_status")
    status = status if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599 else None
    provider_code = details.get("provider_error_code")
    provider_code = provider_code if isinstance(provider_code, str) and _PROVIDER_CODE.fullmatch(provider_code) else None
    stage = details.get("stage")
    if stage not in {"model_setup", "model_invoke", "unit_session"}:
        return None
    fact = UnitModelFailure(issue.code, stage, status, provider_code)
    return ExecutionFailureEvidence(
        origin=ExecutionFailureOrigin.MODEL_CALL if issue.code in MODEL_CALL_FAILURE_CODES else ExecutionFailureOrigin.UNKNOWN,
        code=issue.code,
        dependency="model" if issue.code in MODEL_CALL_FAILURE_CODES else None,
        http_status=status,
        provider_error_code=provider_code,
        diagnostic_message=fact.message,
    )


def _status(cause: Exception) -> int | None:
    """只读取 SDK HTTP 响应或协议校验异常携带的真实状态。"""

    status = cause.status_code
    return status if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599 else None


def _provider_code(cause: Exception) -> str | None:
    """仅读取 SDK 的结构化错误字段并按白名单收窄。"""

    value = cause.code if isinstance(cause, openai.APIStatusError) else cause.type
    return value if isinstance(value, str) and _PROVIDER_CODE.fullmatch(value) else None
