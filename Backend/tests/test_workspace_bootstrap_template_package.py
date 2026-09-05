from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError
from app.services.workspace_bootstrap.template_package import validate_template_package


class TemplatePackageTests(unittest.TestCase):
    """验证首次模板 ZIP 只能含冻结的三个顶层范围。"""

    def test_accepts_fixed_roots_and_unique_template_state(self) -> None:
        """确认完整 frontend/backend ZIP 能通过 Package Contract。"""

        package = self._archive({"frontend/package.json": "{}", "backend/pom.xml": "<project/>", ".xcodeagent/template-state.json": json.dumps(self._state())})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "template.zip"
            path.write_bytes(package)
            result = validate_template_package(path, self._limits())
            self.assertEqual(result.template_state["templateRevision"], "R1")

    def test_rejects_unmanaged_and_internal_paths(self) -> None:
        """确认额外 root、额外 .xcodeagent 与路径穿越均被拒绝。"""

        cases = ("scripts/setup.sh", ".xcodeagent/application.json", "../escape")
        for entry in cases:
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "template.zip"
                path.write_bytes(self._archive({"frontend/package.json": "{}", "backend/pom.xml": "<project/>", ".xcodeagent/template-state.json": json.dumps(self._state()), entry: "bad"}))
                with self.assertRaises(TemplatePackageError):
                    validate_template_package(path, self._limits())

    def _limits(self) -> ArchiveLimits:
        """返回适合小型 fixture 的 ZIP 配额。"""

        return ArchiveLimits(max_package_bytes=1024 * 1024, max_files=20, max_extracted_bytes=1024 * 1024)

    def _state(self) -> dict[str, object]:
        """构造 Engine State fixture。"""

        return {"templateRevision": "R1", "managedFiles": {}, "requested": {}, "effective": {}}

    def _archive(self, entries: dict[str, str]) -> bytes:
        """构造内存 ZIP，便于精确覆盖非法路径。"""

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as package:
            for path, value in entries.items():
                package.writestr(path, value)
        return output.getvalue()
