"""Endpoint 字段映射跨迭代交接的回归。

`plans/endpoints/` 会随新迭代被清空，若不交接，用户在上一轮确认好的字段映射
会在新迭代里全部变成"待配置"。
"""

import tempfile
import unittest
from pathlib import Path

from app.services.endpoint_design_carryover import (
    collect_endpoint_design_files,
    endpoint_design_carryover_path,
    read_endpoint_design_carryover,
    restore_endpoint_design_files,
    write_endpoint_design_carryover,
)


class EndpointDesignCarryoverTests(unittest.TestCase):
    """验证成对收集、登记与恢复，以及半份设计不予沿用。"""

    def test_only_complete_pairs_are_carried_and_restored(self) -> None:
        """JSON 与 Markdown 必须成对存在；恢复后落在规范目录。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            directory = root / ".devagentstudio" / "plans" / "endpoints"
            directory.mkdir(parents=True)
            (directory / "a.json").write_text('{"endpointId":"a"}', encoding="utf-8")
            (directory / "a.md").write_text("# a\n", encoding="utf-8")
            # 只有 JSON 的半份设计不能沿用，否则恢复出来仍是"双文件不完整"。
            (directory / "b.json").write_text('{"endpointId":"b"}', encoding="utf-8")

            files = collect_endpoint_design_files(root / ".devagentstudio")
            self.assertEqual(sorted(files), ["a.json", "a.md"])

            write_endpoint_design_carryover(root, files=files, source_branch="v1_2")
            self.assertEqual(sorted(read_endpoint_design_carryover(root)), ["a.json", "a.md"])

            for item in directory.iterdir():
                item.unlink()
            directory.rmdir()

            self.assertEqual(restore_endpoint_design_files(root), 2)
            self.assertEqual(sorted(path.name for path in directory.iterdir()), ["a.json", "a.md"])

    def test_empty_carryover_clears_previous_record(self) -> None:
        """没有可登记的设计时必须清掉旧记录，避免更早一轮的映射被错误恢复。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            write_endpoint_design_carryover(root, files={"a.json": "{}", "a.md": "# a"}, source_branch="v1_2")
            self.assertTrue(endpoint_design_carryover_path(root).is_file())

            write_endpoint_design_carryover(root, files={}, source_branch="v1_3")
            self.assertFalse(endpoint_design_carryover_path(root).is_file())

    def test_restore_never_overwrites_newer_design(self) -> None:
        """新一轮已生成同名设计时保留新版本，旧交接只做兜底。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            write_endpoint_design_carryover(root, files={"a.json": "old", "a.md": "old"}, source_branch="v1_2")
            directory = root / ".devagentstudio" / "plans" / "endpoints"
            directory.mkdir(parents=True)
            (directory / "a.json").write_text("new", encoding="utf-8")
            (directory / "a.md").write_text("new", encoding="utf-8")

            self.assertEqual(restore_endpoint_design_files(root), 0)
            self.assertEqual((directory / "a.json").read_text(encoding="utf-8"), "new")


if __name__ == "__main__":
    unittest.main()
