from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.page_templates import (
    SOURCE_PAGE_TEMPLATES_DIR,
    load_template_source,
    resolve_page_templates_dir,
    validate_page_templates,
)


class PageTemplatePackagingTests(unittest.TestCase):
    def test_source_templates_load_by_manifest_id(self) -> None:
        """源码模式下前端展示的每个模板都有后端可读取的 TSX。"""

        self.assertEqual(validate_page_templates(), SOURCE_PAGE_TEMPLATES_DIR)
        for entry in SOURCE_PAGE_TEMPLATES_DIR.iterdir():
            if not entry.is_dir():
                continue
            manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                load_template_source(manifest["id"]),
                (entry / "index.tsx").read_text(encoding="utf-8"),
            )

    def test_frozen_loader_uses_packaged_templates(self) -> None:
        """冻结模式只读取 _MEIPASS 中的模板，而不依赖仓库目录。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_dir = root / "app" / "page_templates" / "sample"
            template_dir.mkdir(parents=True)
            (template_dir / "manifest.json").write_text(
                '{"id":"sample"}', encoding="utf-8"
            )
            (template_dir / "index.tsx").write_text(
                "export default function Sample() { return null }", encoding="utf-8"
            )
            with patch.object(sys, "_MEIPASS", str(root), create=True):
                packaged_root = root.resolve() / "app" / "page_templates"
                self.assertEqual(resolve_page_templates_dir(), packaged_root)
                self.assertEqual(validate_page_templates(), packaged_root)
                self.assertIn("function Sample", load_template_source("sample"))
                with self.assertRaisesRegex(ValueError, "未找到"):
                    load_template_source("commonTable")

    def test_missing_packaged_template_fails_validation(self) -> None:
        """冻结资源丢失时应明确失败，不回退到开发目录。"""

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys, "_MEIPASS", directory, create=True):
                with self.assertRaisesRegex(RuntimeError, "目录不存在"):
                    validate_page_templates()
                with self.assertRaisesRegex(ValueError, "目录不存在"):
                    load_template_source("commonTable")

    def test_missing_template_source_fails_validation(self) -> None:
        """只有 manifest 而没有 TSX 时启动校验必须拒绝该模板包。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template_dir = root / "sample"
            template_dir.mkdir()
            (template_dir / "manifest.json").write_text(
                '{"id":"sample"}', encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "资源不完整"):
                validate_page_templates(root)


if __name__ == "__main__":
    unittest.main()
