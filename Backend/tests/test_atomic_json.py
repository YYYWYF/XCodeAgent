from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.utils.atomic_json import atomic_write_json


class AtomicJsonTests(unittest.TestCase):
    """验证共享 JSON 原子写入在当前操作系统上的落盘行为。"""

    def test_atomic_write_json_supports_nested_directory_on_windows(self) -> None:
        """Windows 写入目录内文件时不得因目录级 fsync 产生 PermissionError。"""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / ".xcodeagent" / "template-state.json"

            atomic_write_json(target, {"schemaVersion": 2, "templateRevision": "template-r1"})

            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"schemaVersion": 2, "templateRevision": "template-r1"},
            )
            self.assertEqual(list(target.parent.glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
