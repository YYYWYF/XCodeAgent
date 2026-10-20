"""V4 增量包的无摘要严格合同测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.services.template_reconcile.update_package_v4 import validate_update_package_v4
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError


def _metadata() -> dict:
    """构造最小 V4 元数据样本。"""

    return {
        "protocolVersion": "4", "packageId": "pkg-550e8400-e29b-41d4-a716-446655440000",
        "mode": "APPLY", "sourceRevision": "2026.10.20.1", "expectedStateSequence": 0,
        "operations": [], "validationPlan": [], "payloadManifest": {}, "diagnostics": [],
        "nextTemplateState": {
            "schemaVersion": 3, "templateRevision": "2026.10.20.1", "stateSequence": 1,
            "requested": {}, "effective": {}, "installedArtifacts": {}, "managedContributions": {},
        },
    }


class TemplateUpdateV4Tests(unittest.TestCase):
    """验证 V4 包不接受 V3 摘要字段。"""

    def setUp(self) -> None:
        """创建临时 ZIP 目录。"""

        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "update.zip"

    def tearDown(self) -> None:
        """清理临时目录。"""

        self.temporary.cleanup()

    def _write(self, metadata: dict) -> None:
        """以真实 ZIP 格式写入元数据文件。"""

        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr("extension-update-package.json", json.dumps(metadata))

    def test_accepts_size_only_v4(self) -> None:
        """合法无摘要 V4 包可通过严格预检。"""

        self._write(_metadata())
        validated = validate_update_package_v4(self.path, ArchiveLimits(100000, 10, 100000))
        self.assertEqual(validated.package.expectedStateSequence, 0)

    def test_rejects_v3_digest_fields(self) -> None:
        """摘要字段不可混入 V4 包。"""

        metadata = _metadata()
        metadata["currentStateDigest"] = "sha256:" + "0" * 64
        self._write(metadata)
        with self.assertRaises(TemplatePackageError):
            validate_update_package_v4(self.path, ArchiveLimits(100000, 10, 100000))

    def test_rejects_managed_configuration_snapshot(self) -> None:
        """下一状态不能携带旧敏感参数快照。"""

        metadata = _metadata()
        metadata["nextTemplateState"]["managedConfiguration"] = {"privateKey": "secret"}
        self._write(metadata)
        with self.assertRaises(TemplatePackageError):
            validate_update_package_v4(self.path, ArchiveLimits(100000, 10, 100000))


if __name__ == "__main__":
    unittest.main()
