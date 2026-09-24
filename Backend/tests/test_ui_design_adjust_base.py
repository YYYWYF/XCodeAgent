"""多页调整必须以**磁盘最新 manifest** 为基准，不能用 checkpoint 快照。

后台生成池只写磁盘、不写 checkpoint。所以 checkpoint 里可能还留着某页入队时的
queued/generating，而池早已把它写成 confirmed。`_apply_adjust_pages` 的语义是
"选中页调整、其余页原样保留"，拿过期的 checkpoint 当基准会把池已完成的页**回退**
成 queued —— 池里没有对应任务，那一页就永远卡在「生成中」，点刷新也救不回来
（自愈重入队依赖的正是这份状态）。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.graph.nodes.ui_confirmation import _adjust_base_pages

_SPECS_DIR = ".devagentstudio/specs"


def _workspace(root: Path, pages: list[dict]) -> Path:
    specs = root / _SPECS_DIR
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "ui-designs.json").write_text(
        json.dumps({"schemaVersion": 1, "pages": pages}, ensure_ascii=False), encoding="utf-8"
    )
    return root


def _page(page_id: str, status: str) -> dict:
    return {"pageId": page_id, "status": status, "page_key": page_id.title().replace("_", "")}


class AdjustBasePagesTests(unittest.TestCase):
    def test_prefers_disk_over_stale_checkpoint(self) -> None:
        """核心回归：池已写 confirmed，checkpoint 还停在 queued —— 必须用磁盘的。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = _workspace(
                Path(raw),
                [_page("page_home", "confirmed"), _page("page_hello_agent", "confirmed")],
            )
            checkpoint = [
                _page("page_home", "confirmed"),
                _page("page_hello_agent", "queued"),  # 入队时的旧值
            ]

            base = _adjust_base_pages({"workspace": str(workspace)}, checkpoint)

            self.assertEqual(
                [page["status"] for page in base],
                ["confirmed", "confirmed"],
                "池已完成的页被回退成 queued，那一页会永远卡在生成中",
            )

    def test_falls_back_to_checkpoint_when_disk_missing(self) -> None:
        """从未落盘（首轮）时退回 checkpoint，不能因此空手而归。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            (workspace / _SPECS_DIR).mkdir(parents=True)
            checkpoint = [_page("page_home", "confirmed")]

            base = _adjust_base_pages({"workspace": str(workspace)}, checkpoint)

            self.assertEqual(base, checkpoint)

    def test_falls_back_when_disk_manifest_has_no_pages(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = _workspace(Path(raw), [])
            checkpoint = [_page("page_home", "pending")]

            base = _adjust_base_pages({"workspace": str(workspace)}, checkpoint)

            self.assertEqual(base, checkpoint)

    def test_drops_non_dict_entries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            specs = Path(raw) / _SPECS_DIR
            specs.mkdir(parents=True)
            (specs / "ui-designs.json").write_text(
                json.dumps({"pages": [_page("page_home", "confirmed"), "junk", None]}),
                encoding="utf-8",
            )

            base = _adjust_base_pages({"workspace": str(raw)}, [])

            self.assertEqual([page["pageId"] for page in base], ["page_home"])

    def test_corrupt_manifest_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            specs = Path(raw) / _SPECS_DIR
            specs.mkdir(parents=True)
            (specs / "ui-designs.json").write_text("{ not json", encoding="utf-8")
            checkpoint = [_page("page_home", "confirmed")]

            base = _adjust_base_pages({"workspace": str(raw)}, checkpoint)

            self.assertEqual(base, checkpoint)
