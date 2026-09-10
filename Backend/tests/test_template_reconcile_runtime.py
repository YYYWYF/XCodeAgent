"""Template Reconcile Runtime、健康检查与 Update Package 的回归测试。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.services.template_reconcile.diagnostics import (
    capture_template_update_diagnostic,
    capture_workspace_snapshot,
    diagnostic_paths,
    save_template_update_diagnostic,
    template_update_diagnostic_path,
)
from app.services.template_reconcile.health import assert_managed_workspace_healthy
from app.services.template_reconcile.models import ChangeSetBody, TemplateState
from app.services.template_reconcile.runtime_state import (
    ReconcileAttempt,
    load_reconcile_attempt,
    save_reconcile_attempt,
)
from app.services.template_reconcile.update_package import validate_update_package
from app.services.workspace_bootstrap.models import (
    ArchiveLimits,
    TemplatePackageDownload,
    TemplatePackageError,
    TemplateStateError,
)


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

    def test_health_preserves_crlf_when_comparing_engine_content(self) -> None:
        """Engine State 与磁盘原始 UTF-8 内容同为 CRLF 时不得被误判为漂移。"""

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "frontend/src/index.css"
            target.parent.mkdir(parents=True)
            content = "body {\r\n  color: black;\r\n}\r\n"
            target.write_bytes(content.encode("utf-8"))
            assert_managed_workspace_healthy(
                temporary,
                {"managedFiles": {"frontend/src/index.css": content}},
            )

    def test_failure_diagnostic_preserves_hashes_without_file_content(self) -> None:
        """失败摘要必须区分基线、操作和目标 State，且不能泄露任何文件正文。"""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "frontend/src/index.css"
            target.parent.mkdir(parents=True)
            target.write_text("bootstrap-content", encoding="utf-8")
            current = TemplateState.model_validate(
                {
                    "templateRevision": "2026.09.04.1",
                    "managedFiles": {"frontend/src/index.css": "expected-baseline"},
                    "requested": {},
                    "effective": {},
                }
            )
            next_state = TemplateState.model_validate(
                {
                    "templateRevision": "2026.09.04.1",
                    "managedFiles": {"frontend/src/index.css": "target-content"},
                    "requested": {},
                    "effective": {},
                }
            )
            change_set = ChangeSetBody.model_validate(
                {
                    "operations": [
                        {
                            "type": "UPDATE_FILE",
                            "path": "frontend/src/index.css",
                            "content": "operation-content",
                        }
                    ]
                }
            )
            before = capture_workspace_snapshot(root, diagnostic_paths(current, next_state, change_set))
            target.write_text("operation-content", encoding="utf-8")
            diagnostic = capture_template_update_diagnostic(
                root,
                change_id="change-1",
                technical_plan_sha256="plan-sha",
                download=TemplatePackageDownload(root / "update.zip", "package-sha", 123, "application/zip"),
                current_state=current,
                next_state=next_state,
                change_set=change_set,
                failure_type="TemplateStateError",
                before=before,
            )
            save_template_update_diagnostic(root, diagnostic)
            raw = template_update_diagnostic_path(root).read_text(encoding="utf-8")
            persisted = json.loads(raw)
            entry = persisted["managedFiles"][0]
            self.assertEqual(
                persisted["package"],
                {"sha256": "package-sha", "bytes": 123, "validated": True},
            )
            self.assertEqual(persisted["operations"][0]["type"], "UPDATE_FILE")
            self.assertEqual(
                persisted["operations"][0]["contentSha256"],
                hashlib.sha256(b"operation-content").hexdigest(),
            )
            self.assertEqual(
                entry["currentStateContentSha256"],
                hashlib.sha256(b"expected-baseline").hexdigest(),
            )
            self.assertEqual(
                entry["actualBefore"]["sha256"],
                hashlib.sha256(b"bootstrap-content").hexdigest(),
            )
            self.assertEqual(
                entry["actualAfterFailure"]["sha256"],
                hashlib.sha256(b"operation-content").hexdigest(),
            )
            self.assertNotIn("bootstrap-content", raw)
            self.assertNotIn("operation-content", raw)
            self.assertNotIn("target-content", raw)

    def test_failure_diagnostic_keeps_download_evidence_for_invalid_package(self) -> None:
        """无法解析的 ZIP 也必须留下下载包哈希，但不能臆造操作或目标 State。"""

        with tempfile.TemporaryDirectory() as temporary:
            current = TemplateState.model_validate(
                {
                    "templateRevision": "2026.09.04.1",
                    "managedFiles": {},
                    "requested": {},
                    "effective": {},
                }
            )
            diagnostic = capture_template_update_diagnostic(
                temporary,
                change_id="change-invalid-package",
                technical_plan_sha256="plan-sha",
                download=TemplatePackageDownload(Path(temporary) / "update.zip", "invalid-package-sha", 456, "application/zip"),
                current_state=current,
                next_state=None,
                change_set=None,
                failure_type="TemplatePackageError",
            )
            self.assertEqual(
                diagnostic["package"],
                {"sha256": "invalid-package-sha", "bytes": 456, "validated": False},
            )
            self.assertEqual(diagnostic["operations"], [])
            self.assertEqual(diagnostic["managedFiles"], [])

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
