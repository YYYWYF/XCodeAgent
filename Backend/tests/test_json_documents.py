from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.workspace.json_documents import write_json_atomic


class AtomicJsonWriterTests(unittest.TestCase):
    """验证通用 JSON writer 的原子替换与失败保护语义。"""

    def test_first_write_creates_valid_json(self) -> None:
        """首次写入应创建父目录并生成完整 JSON。"""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "document.json"

            write_json_atomic(target, {"name": "测试", "items": [1, 2]})

            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"name": "测试", "items": [1, 2]},
            )
            self.assertEqual(self._temporary_files(target), [])

    def test_existing_target_is_replaced(self) -> None:
        """再次写入应原子替换已有目标内容。"""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "document.json"
            target.write_text('{"version": 1}\n', encoding="utf-8")

            write_json_atomic(target, {"version": 2})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 2})
            self.assertEqual(self._temporary_files(target), [])

    def test_serialization_failure_preserves_target_without_temp_file(self) -> None:
        """序列化失败应发生在创建临时文件前并保留旧目标。"""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "document.json"
            original = '{"version": 1}\n'
            target.write_text(original, encoding="utf-8")

            with self.assertRaises(TypeError):
                write_json_atomic(target, {"unsupported": object()})

            self.assertEqual(target.read_text(encoding="utf-8"), original)
            self.assertEqual(self._temporary_files(target), [])

    def test_write_failure_preserves_target_and_cleans_temp_file(self) -> None:
        """临时文件写入失败不得破坏旧目标，并应清理临时文件。"""

        class FailingStream:
            """模拟进入上下文后立即写入失败的文本流。"""

            def __enter__(self) -> FailingStream:
                """返回用于 with 语句的失败流。"""

                return self

            def __exit__(self, *_args: object) -> None:
                """退出失败流上下文。"""

            def write(self, _content: str) -> None:
                """模拟底层存储写入错误。"""

                raise OSError("write failed")

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "document.json"
            original = '{"version": 1}\n'
            target.write_text(original, encoding="utf-8")

            def failing_fdopen(descriptor: int, *_args: object, **_kwargs: object) -> FailingStream:
                """关闭真实描述符后返回可控的失败流，避免测试泄漏资源。"""

                os.close(descriptor)
                return FailingStream()

            with patch("app.workspace.json_documents.os.fdopen", side_effect=failing_fdopen):
                with self.assertRaisesRegex(OSError, "write failed"):
                    write_json_atomic(target, {"version": 2})

            self.assertEqual(target.read_text(encoding="utf-8"), original)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 1})
            self.assertEqual(self._temporary_files(target), [])

    def test_replace_failure_preserves_target_and_cleans_temp_file(self) -> None:
        """原子替换失败应保留旧目标并清理完整临时文件。"""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "document.json"
            original = '{"version": 1}\n'
            target.write_text(original, encoding="utf-8")

            with patch(
                "app.workspace.json_documents.os.replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    write_json_atomic(target, {"version": 2})

            self.assertEqual(target.read_text(encoding="utf-8"), original)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 1})
            self.assertEqual(self._temporary_files(target), [])

    def test_cleanup_failure_does_not_mask_replace_failure(self) -> None:
        """临时文件无法删除时仍应抛出原始替换异常。"""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "document.json"
            original = '{"version": 1}\n'
            target.write_text(original, encoding="utf-8")

            with (
                patch(
                    "app.workspace.json_documents.os.replace",
                    side_effect=OSError("replace failed"),
                ),
                patch(
                    "app.workspace.json_documents.Path.unlink",
                    side_effect=OSError("cleanup failed"),
                ),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    write_json_atomic(target, {"version": 2})

            self.assertEqual(target.read_text(encoding="utf-8"), original)

    @staticmethod
    def _temporary_files(target: Path) -> list[Path]:
        """返回指定目标可能遗留的同目录临时文件。"""

        return list(target.parent.glob(f".{target.name}.*.tmp"))


if __name__ == "__main__":
    unittest.main()
