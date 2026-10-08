from __future__ import annotations

import unittest

from app.domain.execution_recovery import (
    ExecutionFailureEvidence,
    ExecutionFailureOrigin,
    execution_failure_sha256,
)
from app.services.execution_failure_classifier import (
    classify_execution_failure,
    public_failure_diagnostic,
    sanitize_failure_diagnostic,
)
from app.services.workspace_bootstrap.models import TemplateEngineError, TemplateStateError


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

    def test_template_validation_failure_is_not_a_model_call(self) -> None:
        """模板运行校验失败属于模板业务失败，保留原节点与安全诊断。"""

        failure = classify_execution_failure(
            TemplateStateError("PROJECT_LAUNCH_FAILED：前端依赖安装失败。"),
            operation="template_reconcile",
        )
        self.assertEqual(failure.origin, ExecutionFailureOrigin.BUSINESS)
        self.assertEqual(failure.dependency, "template")
        self.assertEqual(failure.code, "templatestateerror")
        self.assertEqual(failure.operation, "template_reconcile")
        self.assertTrue(failure.replay_compatible)
        self.assertEqual(failure.diagnostic_message, "PROJECT_LAUNCH_FAILED：前端依赖安装失败。")

    def test_template_engine_error_is_not_a_model_call(self) -> None:
        """模板配置与 HTTP 错误保留真实原因及重试属性，不受 models 模块名误导。"""

        for status in (None, 403, 503):
            with self.subTest(status=status):
                failure = classify_execution_failure(
                    TemplateEngineError("Template Engine 地址未配置。", http_status=status),
                    operation="template_reconcile",
                )
                self.assertEqual(failure.origin, ExecutionFailureOrigin.EXTERNAL_DEPENDENCY)
                self.assertEqual(failure.dependency, "template_engine")
                self.assertEqual(failure.code, f"http_{status}" if status else "templateengineerror")
                self.assertEqual(failure.operation, "template_reconcile")
                self.assertEqual(failure.http_status, status)
                self.assertTrue(failure.replay_compatible)
                self.assertIsNone(failure.model)
                self.assertEqual(failure.diagnostic_message, "Template Engine 地址未配置。")

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

    def test_diagnostic_message_redacts_quoted_key_value_secrets(self) -> None:
        """JSON 和 Python dict 格式的凭据值也必须从诊断摘要中移除。"""

        cases = (
            ('{"api_key":"key-secret"}', "key-secret"),
            ("{'token': 'token-secret'}", "token-secret"),
            ('{"access_token":"access-secret"}', "access-secret"),
            ("{'x-api-key': 'anthropic-secret'}", "anthropic-secret"),
        )

        for message, secret in cases:
            with self.subTest(message=message):
                diagnostic = sanitize_failure_diagnostic(RuntimeError(message))

                self.assertIsNotNone(diagnostic)
                assert diagnostic is not None
                self.assertNotIn(secret, diagnostic)

        diagnostic = sanitize_failure_diagnostic(
            RuntimeError("upstream model failed while loading configuration")
        )
        self.assertEqual(
            diagnostic, "upstream model failed while loading configuration"
        )

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
                base.model_copy(update={
                    "diagnostic_message": "model unavailable",
                    "provider_error_code": "rate_limit",
                    "stage": "model_invoke",
                })
            ),
        )

    def test_old_durable_failure_without_stage_remains_unmodified(self) -> None:
        """历史 Durable Evidence 缺少可选 stage 时继续可读且不推断阶段。"""

        failure = ExecutionFailureEvidence.model_validate({
            "origin": "model_call",
            "code": "UNIT_GENERATION_INFRASTRUCTURE_FAILURE",
            "dependency": "model",
            "http_status": None,
            "diagnostic_message": "历史摘要",
        })

        projected = public_failure_diagnostic(failure, source_run_id="run-old")
        self.assertIsNone(failure.stage)
        self.assertNotIn("stage", projected)
        self.assertEqual(projected["message"], "历史摘要")


__all__ = ["ExecutionFailureClassifierTests"]
