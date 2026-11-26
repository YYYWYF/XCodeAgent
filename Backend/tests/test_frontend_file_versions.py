"""验证 Windows 路径与句柄 ctime 不同，以及真实变化仍被拒绝。"""

import os
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.agents.frontend.file_backend import FILE_ACCESS, FrontendScopedFilesystemBackend, file_revision
from tests.test_frontend_build_execution import _PAGE, _create_file
from tests.test_frontend_step_convergence import _advance, _begin


def _stat_view(info, **changes):
    """保留真实元数据，仅覆盖用于模拟系统差异或并发变化的字段。"""
    values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
    return SimpleNamespace(**(values | changes))


class FrontendFileVersionTests(unittest.TestCase):
    """在真实文件上注入 Windows 元数据差异，检查工具与验收行为。"""

    def test_windows_ctime_difference_allows_write_edit_and_acceptance(self):
        """句柄与路径 ctime 不同但各自稳定时，连续写入编辑和验收成功。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            original = "export default function Page() { return null; }\r\n"
            file = _create_file(root, _PAGE, "")
            file.write_bytes(original.encode())
            scope = _begin(root)
            _advance(scope)
            backend = FrontendScopedFilesystemBackend(scope)
            real_fstat = os.fstat

            def windows_fstat(fd):
                """模拟 Windows 句柄查询使用不同的 ctime 语义。"""
                info = real_fstat(fd)
                return _stat_view(info, st_ctime_ns=info.st_ctime_ns + 10_000_000_000)

            token = FILE_ACCESS.set((scope, _PAGE))
            try:
                with patch("app.agents.frontend.file_backend.os.fstat", side_effect=windows_fstat):
                    self.assertIsNone(backend.write(_PAGE, original).error)
                    self.assertTrue(scope.mutation_notice["no_change"])
                    self.assertIsNone(backend.write(_PAGE, original.replace("null", "1")).error)
                    self.assertIsNone(backend.edit(_PAGE, "return 1", "return 2").error)
                    self.assertEqual(file.read_bytes(), original.replace("null", "2").encode())
                    findings = scope.checks.run(structure_only=True)
                    self.assertEqual(findings[0]["status"], "passed")
                    self.assertEqual(findings[0]["file_version"], sha256(file.read_bytes()).hexdigest())
            finally:
                FILE_ACCESS.reset(token)

    def test_handle_ctime_change_blocks_write_at_both_checks(self):
        """即使路径版本稳定，句柄 ctime 在读取后或最终写入前变化仍阻断。"""
        for change_at in (2, 3):
            with self.subTest(change_at=change_at), TemporaryDirectory() as folder:
                root = Path(folder)
                file = _create_file(root, _PAGE, "export default function Page() { return null; }")
                original = file.read_bytes()
                scope = _begin(root)
                _advance(scope)
                backend = FrontendScopedFilesystemBackend(scope)
                real_fstat = os.fstat
                calls = 0

                def changed_fstat(fd):
                    """在指定检查点模拟句柄元数据发生真实变化。"""
                    nonlocal calls
                    calls += 1
                    info = real_fstat(fd)
                    return _stat_view(info, st_ctime_ns=info.st_ctime_ns + int(calls >= change_at))

                token = FILE_ACCESS.set((scope, _PAGE))
                try:
                    with patch("app.agents.frontend.file_backend.os.fstat", side_effect=changed_fstat):
                        self.assertIn("文件已变化", backend.write(_PAGE, "replacement").error)
                    self.assertEqual(file.read_bytes(), original)
                finally:
                    FILE_ACCESS.reset(token)

    def test_read_detects_handle_change_after_reading(self):
        """验收读取后再次核对句柄，不能把变化中的源码当作稳定证据。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            _create_file(root, _PAGE, "export default function Page() { return null; }")
            scope = _begin(root)
            real_fstat = os.fstat
            calls = 0

            def changed_fstat(fd):
                """只在第二次句柄查询时改变 ctime，路径查询保持不变。"""
                nonlocal calls
                calls += 1
                info = real_fstat(fd)
                return _stat_view(info, st_ctime_ns=info.st_ctime_ns + int(calls > 1))

            with patch("app.agents.frontend.file_backend.os.fstat", side_effect=changed_fstat):
                with self.assertRaisesRegex(ValueError, "读取期间文件版本已变化"):
                    scope.checks._read(_PAGE)

    def test_path_ctime_change_still_blocks_write_and_read(self):
        """句柄稳定而路径查询前后 ctime 改变时，仍拒绝写入和验收。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            file = _create_file(root, _PAGE, "export default function Page() { return null; }")
            original = file.read_bytes()
            scope = _begin(root)
            _advance(scope)
            backend = FrontendScopedFilesystemBackend(scope)
            # 定向覆盖路径版本查询，不改变句柄版本，证明两套基线独立校验。
            info = file.stat()
            changed = _stat_view(info, st_ctime_ns=info.st_ctime_ns + 1)
            changed_path = Mock(wraps=file)
            changed_path.stat.return_value = changed
            token = FILE_ACCESS.set((scope, _PAGE))
            try:
                with patch.object(backend, "_resolve_path", side_effect=[file, changed_path]):
                    self.assertIn("文件已变化", backend.write(_PAGE, "replacement").error)
                with patch.object(scope.checks, "_version", return_value=file_revision(changed)):
                    with self.assertRaisesRegex(ValueError, "检查期间文件版本已变化"):
                        scope.checks._read(_PAGE)
                self.assertEqual(file.read_bytes(), original)
            finally:
                FILE_ACCESS.reset(token)

    def test_replaced_or_unknown_identity_blocks_write_and_read(self):
        """内容元数据相同但文件身份不同或不可用时，写入与验收均拒绝。"""
        for unknown in (False, True):
            with self.subTest(unknown=unknown), TemporaryDirectory() as folder:
                root = Path(folder)
                file = _create_file(root, _PAGE, "export default function Page() { return null; }")
                original = file.read_bytes()
                scope = _begin(root)
                _advance(scope)
                backend = FrontendScopedFilesystemBackend(scope)
                real_fstat = os.fstat

                def replaced_fstat(fd):
                    """模拟打开时路径已被替换，或系统未提供可用文件编号。"""
                    info = real_fstat(fd)
                    return _stat_view(info, st_ino=0 if unknown else info.st_ino + 1)

                token = FILE_ACCESS.set((scope, _PAGE))
                try:
                    with patch("app.agents.frontend.file_backend.os.fstat", side_effect=replaced_fstat):
                        self.assertIn("文件身份", backend.write(_PAGE, "replacement").error)
                        with self.assertRaisesRegex(ValueError, "身份"):
                            scope.checks._read(_PAGE)
                    self.assertEqual(file.read_bytes(), original)
                finally:
                    FILE_ACCESS.reset(token)

    def test_external_edit_is_not_overwritten(self):
        """候选内容生成期间文件被真实修改时，保留外部修改并拒绝写回。"""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            file = _create_file(root, _PAGE, "export default function Page() { return 1; }")
            scope = _begin(root)
            _advance(scope)
            backend = FrontendScopedFilesystemBackend(scope)
            external = b"export default function Page() { return 9; }"

            def external_edit(*args):
                """用等长真实修改模拟其他写入者，不依赖文件大小变化。"""
                file.write_bytes(external)
                return "export default function Page() { return 2; }", 1

            token = FILE_ACCESS.set((scope, _PAGE))
            try:
                with patch("app.agents.frontend.file_backend._replacement", side_effect=external_edit):
                    self.assertIn("文件已变化", backend.edit(_PAGE, "return 1", "return 2").error)
                self.assertEqual(file.read_bytes(), external)
            finally:
                FILE_ACCESS.reset(token)
