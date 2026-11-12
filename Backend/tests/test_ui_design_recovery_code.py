"""重新打开工作区时，确认界面必须拿得到设计稿源码。

正式 manifest 落盘时刻意剥离了 `code`（源码是运行时数据，不入库），但确认界面要靠它
渲染预览、「查看设计稿」按钮也按它判可用性。recovery 只读 checkpoint + 磁盘 manifest，
不回填的话每一页都点不开 —— 本轮新生成的和从上一轮继承来的一视同仁。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.protocols.application_page_planning import _with_page_code

_PAGE_KEY = "WelcomeHome"
_CODE = "export default function PageWelcomeHome() { return <div>hello</div>; }\n"


def _workspace(root: Path, *, with_code: bool = True) -> Path:
    """构造一个带设计稿目录的工作区。"""

    if with_code:
        page_dir = root / ".devagentstudio" / "ui-design" / "pages" / _PAGE_KEY
        page_dir.mkdir(parents=True)
        (page_dir / "index.tsx").write_text(_CODE, encoding="utf-8")
    return root


def _manifest(*page_keys: str) -> dict:
    """正式 manifest：不含 code，只有 page_key。"""

    return {
        "schema_version": "ui-manifest.v3",
        "confirmation_status": "pending_user_confirmation",
        "pages": [
            {"pageId": key.lower(), "page_key": key, "status": "confirmed"}
            for key in page_keys
        ],
    }


class WithPageCodeTests(unittest.TestCase):
    def test_reattaches_code_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = _workspace(Path(raw))

            enriched = _with_page_code(_manifest(_PAGE_KEY), {"workspace": str(workspace)})

            self.assertEqual(enriched["pages"][0]["code"], _CODE)
            # 其余字段原样保留
            self.assertEqual(enriched["pages"][0]["status"], "confirmed")
            self.assertEqual(enriched["schema_version"], "ui-manifest.v3")

    def test_missing_file_leaves_page_without_code(self) -> None:
        """本轮还没生成的页面磁盘上没有源码 —— 留空，按钮禁用是正确表现。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = _workspace(Path(raw), with_code=False)

            enriched = _with_page_code(_manifest("HelloAgent"), {"workspace": str(workspace)})

            self.assertFalse(enriched["pages"][0].get("code"))

    def test_existing_code_is_not_overwritten(self) -> None:
        """已经带 code 的条目（生成池刚写完）保持原样，不做多余的磁盘读取。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = _workspace(Path(raw))
            manifest = _manifest(_PAGE_KEY)
            manifest["pages"][0]["code"] = "// 内存里的最新版本"

            enriched = _with_page_code(manifest, {"workspace": str(workspace)})

            self.assertEqual(enriched["pages"][0]["code"], "// 内存里的最新版本")

    def test_page_without_page_key_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = _workspace(Path(raw))
            manifest = {"pages": [{"pageId": "orphan", "status": "pending"}]}

            enriched = _with_page_code(manifest, {"workspace": str(workspace)})

            self.assertFalse(enriched["pages"][0].get("code"))

    def test_malformed_manifest_is_returned_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = _workspace(Path(raw))

            self.assertEqual(_with_page_code({"pages": "nope"}, {"workspace": str(workspace)}), {"pages": "nope"})
