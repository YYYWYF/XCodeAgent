"""Template Reconcile Runtime、健康检查与 Update Package 的回归测试。"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from app.services.template_reconcile.health import assert_managed_workspace_healthy
from app.services.template_reconcile.runtime_state import (
    ReconcileAttempt,
    load_reconcile_attempt,
    save_reconcile_attempt,
)
from app.services.template_reconcile.update_package import validate_update_package
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError, TemplateStateError


class TemplateReconcileRuntimeTests(unittest.TestCase):
    """验证当前 Engine 三类文件操作所需的最小本地事实。"""

    def test_round_trips_minimal_attempt(self) -> None:
        """Runtime State 只保存 Attempt，不复制 TemplateState 基线。"""

        with tempfile.TemporaryDirectory() as temporary:
            attempt = ReconcileAttempt("change-1", "plan-sha", "head-sha", "APPLYING", ("frontend/src/a.ts",))
            save_reconcile_attempt(temporary, attempt)
            self.assertEqual(load_reconcile_attempt(temporary), attempt)
            raw = (Path(temporary) / ".xcodeagent/runtime/template-runtime-state.json").read_text()
            self.assertNotIn("managedBaseline", raw)

    def test_health_rejects_managed_file_drift(self) -> None:
        """受管文件与 State 内容不一致时必须阻断后续 Build 或 Reconcile。"""

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "frontend/src/a.ts"
            target.parent.mkdir(parents=True)
            target.write_text("actual", encoding="utf-8")
            with self.assertRaises(TemplateStateError):
                assert_managed_workspace_healthy(
                    temporary,
                    {"managedFiles": {"frontend/src/a.ts": "expected"}},
                )

    def test_validates_current_update_fixture(self) -> None:
        """当前 fixture 的三类文件 Update Package 必须可以被消费。"""

        fixture = Path(__file__).parent / "fixtures/template_reconcile/update-package"
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "update.zip"
            with zipfile.ZipFile(archive, "w") as package:
                for item in fixture.rglob("*"):
                    if item.is_file():
                        package.write(item, item.relative_to(fixture).as_posix())
            validated = validate_update_package(archive, ArchiveLimits(1024 * 1024, 100, 1024 * 1024))
            self.assertEqual(len(validated.change_set.operations), 3)

    def test_rejects_extra_payload(self) -> None:
        """Package 不得夹带没有对应操作的文件。"""

        fixture = Path(__file__).parent / "fixtures/template_reconcile/update-package"
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "update.zip"
            with zipfile.ZipFile(archive, "w") as package:
                for item in fixture.rglob("*"):
                    if item.is_file():
                        package.write(item, item.relative_to(fixture).as_posix())
                package.writestr("payload/frontend/src/extra.ts", "extra")
            with self.assertRaises(TemplatePackageError):
                validate_update_package(archive, ArchiveLimits(1024 * 1024, 100, 1024 * 1024))
