"""验证 V2 durable Attempt 到 Template Preparation AG-UI 状态的投影。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.template_reconcile.runtime_v2 import ReconcileAttemptV2, persist_prepared_attempt, update_attempt
from app.services.template_reconcile.template_preparation import template_preparation_projection_v2


def _attempt() -> ReconcileAttemptV2:
    """构造可持久化的最小 V2 Attempt fixture。"""

    return ReconcileAttemptV2(
        attempt_id="attempt-1", retry_of="attempt-0", operation_type="UPDATE", mode="RECONCILE",
        protocol_version="2", technical_plan_sha256="sha256:" + "a" * 64,
        package_id="package-1", package_digest="sha256:" + "b" * 64,
        current_state_digest="sha256:" + "c" * 64, next_state_digest="sha256:" + "c" * 64,
        phase="PREPARED", status="RUNNING", started_at="2026-09-10T00:00:00+00:00",
        updated_at="2026-09-10T00:00:00+00:00",
    )


class TemplatePreparationProjectionV2Tests(unittest.TestCase):
    """覆盖刷新后从磁盘恢复进度及失败重试语义。"""

    def test_projects_durable_attempt_progress_and_retry_metadata(self) -> None:
        """读取持久 Attempt 时必须返回完整产品投影，不依赖进程内缓存。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package.zip"
            package.write_bytes(b"package")
            attempt = _attempt()
            persist_prepared_attempt(root, attempt, package)
            projection = template_preparation_projection_v2(root)
            assert projection is not None
            self.assertEqual("attempt-1", projection["attemptId"])
            self.assertEqual("attempt-0", projection["retryOf"])
            self.assertEqual(0, projection["completedOperations"])
            failed = update_attempt(root, attempt, phase="FAILED", status="FAILED", error_code="VALIDATION_FAILED", error_message="验证失败")
            projection = template_preparation_projection_v2(root)
            assert projection is not None
            self.assertEqual(failed.error_code, projection["errorCode"])
            self.assertTrue(projection["retryable"])
            self.assertEqual("验证失败", projection["logs"][-1]["message"])

    def test_missing_v2_attempt_does_not_claim_legacy_progress(self) -> None:
        """不存在 V2 Attempt 时必须返回空，禁止把旧运行态映射为当前协议。"""

        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(template_preparation_projection_v2(directory))
