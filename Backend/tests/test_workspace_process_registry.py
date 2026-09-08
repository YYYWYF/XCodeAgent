from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.workspace_process_registry import WorkspaceProcessRegistry


class WorkspaceProcessRegistryTests(unittest.TestCase):
    """验证应用删除可终止同步命令及其工作区后续启动。"""

    @unittest.skipUnless(os.name == "posix", "POSIX 信号回收场景")
    def test_cancel_run_reaps_repackage_command_ignoring_sigterm(self) -> None:
        """补打包的同步 communicate 也观察停止，不会等到十分钟构建超时。"""
        registry = WorkspaceProcessRegistry()
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            ready = workspace / "ready"
            errors: list[str] = []

            def run_command() -> None:
                """模拟忽略退出信号的 Maven 命令并保存取消结果。"""
                try:
                    registry.run(
                        [sys.executable, "-c", (
                            "import signal,time,pathlib; "
                            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                            f"pathlib.Path({str(ready)!r}).touch(); time.sleep(30)"
                        )], workspace=workspace, run_id="startup", capture_output=True, timeout=600,
                    )
                except RuntimeError as exc:
                    errors.append(str(exc))

            worker = threading.Thread(target=run_command)
            worker.start()
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            registry.cancel_run("startup")
            worker.join(timeout=8)
            self.assertFalse(worker.is_alive())
            self.assertEqual(registry.active_process_ids(workspace), [])
            self.assertEqual(len(errors), 1)
            self.assertIn("已取消", errors[0])

    def test_cancel_workspace_terminates_process_and_blocks_new_commands(self) -> None:
        """删除栅栏应结束已登记命令，并拒绝目标工作区再次启动进程。"""

        registry = WorkspaceProcessRegistry()
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            result_holder: list[object] = []

            def run_command() -> None:
                """在线程中模拟 Graph 同步节点启动的长时间子进程。"""

                result_holder.append(
                    registry.run(
                        [sys.executable, "-c", "import time; time.sleep(30)"],
                        workspace=workspace,
                        capture_output=True,
                    )
                )

            worker = threading.Thread(target=run_command)
            worker.start()
            deadline = time.monotonic() + 5
            while not registry.active_process_ids(workspace) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(registry.active_process_ids(workspace))

            cancellation = registry.cancel_workspace(workspace)
            worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            self.assertEqual(cancellation["remainingProcessIds"], [])
            self.assertEqual(len(result_holder), 1)
            self.assertNotEqual(result_holder[0].returncode, 0)
            with self.assertRaisesRegex(RuntimeError, "正在删除"):
                registry.run(
                    [sys.executable, "-c", "print('unexpected')"],
                    workspace=workspace,
                    capture_output=True,
                )


if __name__ == "__main__":
    unittest.main()
