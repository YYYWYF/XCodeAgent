from __future__ import annotations

import unittest

from app.domain.execution_recovery import (
    ExecutionFailureEvidence,
    ExecutionFailureOrigin,
    execution_failure_sha256,
)
from app.services.execution_failure_classifier import (
    classify_execution_failure,
    sanitize_failure_diagnostic,
)


class FakeModelFailure(Exception):
    """提供带 HTTP 状态和模型字段的脱敏测试异常。"""

    __module__ = "openai"

    def __init__(self, message: str, *, status_code: int = 503) -> None:
        """保存测试用异常消息与状态码。"""

        super().__init__(message)
        self.status_code = status_code
        self.model = "mimo-v2.5-pro"


class ExecutionFailureClassifierTests(unittest.TestCase):
    """覆盖失败摘要的脱敏、限长和 recovery identity 稳定性。"""

    def test_model_failure_keeps_safe_diagnostic_message(self) -> None:
        """模型失败应保留用户可读摘要，但移除常见凭据值。"""

        failure = classify_execution_failure(
            FakeModelFailure(
                "503 Service Unavailable; Authorization: Bearer sk-secret, "
                "api_key=key-secret apiKey=camel-secret token=token-secret "
                "access_token=access-secret"
            ),
            operation="technical_planning",
        )

        self.assertEqual(failure.origin, ExecutionFailureOrigin.MODEL_CALL)
        self.assertEqual(failure.http_status, 503)
        self.assertEqual(failure.model, "mimo-v2.5-pro")
        self.assertIsNotNone(failure.diagnostic_message)
        assert failure.diagnostic_message is not None
        self.assertNotIn("sk-secret", failure.diagnostic_message)
        self.assertNotIn("key-secret", failure.diagnostic_message)
        self.assertNotIn("camel-secret", failure.diagnostic_message)
        self.assertNotIn("token-secret", failure.diagnostic_message)
        self.assertNotIn("access-secret", failure.diagnostic_message)
        self.assertIn("Authorization: [REDACTED]", failure.diagnostic_message)

    def test_diagnostic_message_is_limited_to_2048_characters(self) -> None:
        """过长异常文本必须在进入 Durable Evidence 前被截断。"""

        diagnostic = sanitize_failure_diagnostic(RuntimeError("x" * 4096))

        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertLessEqual(len(diagnostic), 2048)

    def test_diagnostic_message_does_not_change_recovery_identity_hash(self) -> None:
        """同一失败事实的展示文案变化不得改变 source failure identity。"""

        base = ExecutionFailureEvidence(
            origin=ExecutionFailureOrigin.MODEL_CALL,
            code="MODEL_CONNECTION_ERROR",
            operation="technical_planning",
            dependency="model",
            provider="openai-compatible",
            model="mimo-v2.5-pro",
            http_status=503,
            replay_compatible=True,
        )

        self.assertEqual(
            execution_failure_sha256(base),
            execution_failure_sha256(
                base.model_copy(update={"diagnostic_message": "model unavailable"})
            ),
        )


__all__ = ["ExecutionFailureClassifierTests"]
