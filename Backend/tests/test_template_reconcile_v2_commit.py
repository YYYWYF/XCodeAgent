"""验证 V2 State Commit 的 rename 后异常边界。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.template_reconcile.protocol_v2 import TemplateStateV2
from app.services.template_reconcile.runtime_v2 import ReconcileAttemptV2, persist_prepared_attempt
from app.services.template_reconcile.service import StateCommittedV2Error, TemplateReconcileService
from app.services.template_reconcile.state_v2 import (
    load_template_state_v2,
    write_template_state_v2 as _write_template_state_v2,
)


def _state(revision: str) -> TemplateStateV2:
    """构造只用于 Commit 边界验证的最小 V2 State。"""

    return TemplateStateV2.model_validate({
        "schemaVersion": 2,
        "templateRevision": revision,
        "requested": {},
        "effective": {},
        "appliedAdditions": {},
    })


def _attempt() -> ReconcileAttemptV2:
    """构造已经 durable 的 PREPARED Attempt。"""

    return ReconcileAttemptV2(
        attempt_id="attempt-commit", retry_of=None, operation_type="UPDATE", mode="APPLY",
        protocol_version="2", technical_plan_sha256="a" * 64, package_id="package-commit",
        source_revision="r1", package_digest="sha256:" + "b" * 64,
        current_state_digest="sha256:" + "c" * 64, next_state_digest="sha256:" + "d" * 64,
        phase="PREPARED", status="RUNNING", started_at="2026-09-10T00:00:00+00:00",
        updated_at="2026-09-10T00:00:00+00:00",
    )


class TemplateReconcileV2CommitTests(unittest.TestCase):
    """验证 State rename 已成功但调用返回异常时只能 Roll-forward。"""

    def test_rename_after_exception_is_classified_as_state_committed(self) -> None:
        """写入函数在真实 State 落盘后抛错时不得让上层进入 Workspace restore 路径。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = _state("r1")
            next_state = _state("r2")
            _write_template_state_v2(root, current)
            package = root / "package.zip"
            package.write_bytes(b"package")
            attempt = _attempt()
            persist_prepared_attempt(root, attempt, package)

            def write_then_raise(workspace: Path, state: TemplateStateV2) -> None:
                """模拟 os.replace 已完成后底层调用仍向上报告异常。"""

                _write_template_state_v2(workspace, state)
                raise OSError("rename completed before error")

            service = TemplateReconcileService(SimpleNamespace())
            with patch(
                "app.services.template_reconcile.service.write_template_state_v2",
                side_effect=write_then_raise,
            ), self.assertRaises(StateCommittedV2Error):
                service._commit(root, attempt, next_state)

            self.assertEqual("r2", load_template_state_v2(root).templateRevision)
