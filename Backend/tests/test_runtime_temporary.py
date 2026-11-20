"""验证实例锁、异常退出和共享资源的真实磁盘生命周期。"""

from __future__ import annotations

import gc
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.services.agent_file_documents import AGENTS_FILE_NAME
from app.services.agent_memory_runtime import create_agent_memory_runtime_snapshot
from app.services.runtime_temporary import RuntimeTemporaryArea


def isolated_runtime_area(root: Path) -> RuntimeTemporaryArea:
    """为测试创建与用户目录隔离的实例。"""
    return RuntimeTemporaryArea(root / "environment" / "runtime" / "temporary", f"backend-{uuid4().hex}")


class RuntimeTemporaryTests(unittest.TestCase):
    def test_active_process_is_preserved_and_killed_process_is_reaped(self) -> None:
        """用真实子进程核对活跃锁，并在强制结束后重新启动回收。"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            ready = base / "ready"
            instance_id = f"backend-{uuid4().hex}"
            root = base / "environment" / "runtime" / "temporary"
            child_script = """
import sys
from pathlib import Path
from app.services.runtime_temporary import RuntimeTemporaryArea
area = RuntimeTemporaryArea(Path(sys.argv[1]), sys.argv[2])
snapshot = area.directory / 'snapshot'
snapshot.mkdir()
file = snapshot / 'AGENTS.md'
file.write_text('worker', encoding='utf-8')
file.chmod(0o444)
snapshot.chmod(0o555)
Path(sys.argv[3]).touch()
sys.stdin.read()
area.close()
"""
            with subprocess.Popen(
                [sys.executable, "-c", child_script, str(root), instance_id, str(ready)],
                stdin=subprocess.PIPE, stderr=subprocess.PIPE,
            ) as child:
                try:
                    deadline = time.monotonic() + 8
                    while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(ready.exists(), "子进程未完成实例登记")
                    parent = isolated_runtime_area(base)
                    try:
                        self.assertTrue((root / instance_id / "snapshot" / "AGENTS.md").exists())
                        child.kill()
                        child.communicate(timeout=5)
                        restarted = isolated_runtime_area(base)
                        try:
                            self.assertFalse((root / instance_id).exists())
                            self.assertFalse((root / f"{instance_id}.lock").exists())
                            self.assertTrue(parent.directory.exists())
                        finally:
                            restarted.close()
                    finally:
                        parent.close()
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=5)

    def test_unknown_and_linked_entries_are_preserved(self) -> None:
        """忽略无归属目录、伪造记录和含链接的孤儿，保护外部目标。"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            owner = isolated_runtime_area(base)
            unknown = owner.root / f"backend-{uuid4().hex}"
            unknown.mkdir()
            unknown.with_suffix(".lock").write_bytes(b"unknown")
            outside = base / "outside"
            outside.write_text("keep", encoding="utf-8")
            try:
                (owner.directory / "linked").symlink_to(outside)
            except (NotImplementedError, OSError):
                owner.close()
                self.skipTest("当前环境不能创建符号链接")
            with self.assertLogs("app.services.runtime_temporary", level="WARNING"):
                owner.close()
                restarted = isolated_runtime_area(base)
            try:
                self.assertTrue((owner.directory / "linked").is_symlink())
                self.assertTrue(unknown.is_dir())
                self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
            finally:
                restarted.close()

    def test_cleanup_failure_is_retried_after_restart(self) -> None:
        """模拟 Windows 文件占用导致的清理失败，并验证归属记录可供下次重试。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            area = isolated_runtime_area(root)
            (area.directory / "payload").touch()
            with (
                patch("app.services.runtime_temporary.shutil.rmtree", side_effect=PermissionError),
                self.assertLogs("app.services.runtime_temporary", level="WARNING"),
            ):
                area.close()
            self.assertTrue(area.directory.exists())
            restarted = isolated_runtime_area(root)
            try:
                self.assertFalse(area.directory.exists())
            finally:
                restarted.close()

    @unittest.skipUnless(sys.platform == "win32", "需要 Windows 目录联接")
    def test_windows_junction_is_preserved(self) -> None:
        """回收器不能删除目录联接或联接指向的文件。"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            owner = isolated_runtime_area(base)
            outside = base / "outside"
            outside.mkdir()
            (outside / "keep.txt").touch()
            junction = owner.directory / "junction"
            try:
                subprocess.run(
                    ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
                    check=True, capture_output=True,
                )
            finally:
                owner.close()
            restarted = isolated_runtime_area(base)
            try:
                self.assertTrue(junction.is_junction())
                self.assertTrue((outside / "keep.txt").exists())
            finally:
                restarted.close()

    def test_concurrent_memory_snapshots_share_and_release_the_last_reader(self) -> None:
        """多个 Agent 共用同一版本，退出遇到仍在读取的快照时延迟回收。"""
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            area = isolated_runtime_area(base)
            self.addCleanup(area.close)
            source = base / "rules"
            source.mkdir()
            document = source / AGENTS_FILE_NAME
            document.write_text("version one", encoding="utf-8")
            with patch("app.services.runtime_temporary._area", area):
                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = [executor.submit(create_agent_memory_runtime_snapshot, root=source) for _ in range(4)]
                    snapshots = [future.result(timeout=5) for future in futures]
                backend = snapshots[0].backend
                self.assertTrue(all(snapshot.backend is backend for snapshot in snapshots))
                self.assertEqual(len(list(area.directory.iterdir())), 1)
                document.write_text("version two", encoding="utf-8")
                latest = create_agent_memory_runtime_snapshot(root=source).backend
                self.assertEqual(latest.download_files(["/AGENTS.md"])[0].content, b"version two")
                area.close()
                self.assertEqual(backend.download_files(["/AGENTS.md"])[0].content, b"version one")
                self.assertTrue(area.directory.exists())
                del futures, snapshots, backend, latest
                gc.collect()
                self.assertFalse(area.directory.exists())
