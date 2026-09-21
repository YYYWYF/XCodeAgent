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

    def test_text_capture_replaces_invalid_local_encoding_bytes(self) -> None:
        """非法本地编码字节不得让 subprocess reader 线程异常退出。"""

        registry = WorkspaceProcessRegistry()
        with TemporaryDirectory() as directory:
            completed = registry.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.buffer.write(bytes([0x89])); sys.stdout.flush()",
                ],
                workspace=directory,
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )

        self.assertEqual(completed.returncode, 0)
        self.assertIsInstance(completed.stdout, str)
        self.assertTrue(completed.stdout)

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


class ManagedProcessRegistrationTests(unittest.TestCase):
    """_processes 必须全按 list 存。

    回归背景：`managed_process` 曾写成 `setdefault(key, set()).add(process)`，
    与 `__init__` 的 list 类型声明、以及 destroy/cleanup 路径的 `.append` 相矛盾。
    该 key 一旦由 `.append` 建过，setdefault 返回的就是 list，`.add()` 直接抛
    `AttributeError: 'list' object has no attribute 'add'` —— 只在特定时序下触发
    （同一个 workspace 先被别的路径登记过），表现为工作流在 inspect_workspace
    阶段随机失败、且报错只有类型没有位置。
    """

    def test_managed_process_works_after_start(self) -> None:
        """先经 start()（list 路径）登记，再走 managed_process 不得抛 AttributeError。

        这是线上真实的触发时序：应用预览/启动验收先经 `start()` 为工作区登记长生命
        周期进程（建出 list），随后构建期的 `inspect_workspace` 用 `managed_process`
        跑 git 命令，撞上 `setdefault(key, set()).add(...)` 直接抛
        `AttributeError: 'list' object has no attribute 'add'`。

        没有先 `start()` 时该 key 不存在，setdefault 建的是 set，所以不炸 —— 这正是
        它只在特定时序下复现、且报错只有类型没有位置的原因。
        """

        with TemporaryDirectory() as workspace:
            registry = WorkspaceProcessRegistry()
            # 先让 _processes[key] 由 .append 路径建出来（模拟预览进程已登记）。
            started = registry.start(workspace, [sys.executable, "-c", "pass"])
            try:
                with registry.managed_process(
                    [sys.executable, "-c", "pass"], workspace=workspace
                ) as process:
                    self.assertIsNotNone(process.pid)
            finally:
                registry.release(workspace, started)

    def test_managed_process_registers_into_list(self) -> None:
        """登记结果必须是 list：与类型声明及其余用法一致。

        必须在上下文**内**断言：退出时 finally 会回收进程并清掉空 key。
        """

        with TemporaryDirectory() as workspace:
            registry = WorkspaceProcessRegistry()
            with registry.managed_process(
                [sys.executable, "-c", "pass"], workspace=workspace
            ):
                key = next(iter(registry._processes))
                self.assertIsInstance(
                    registry._processes[key],
                    list,
                    "_processes 必须按 list 存，否则 .add 会炸",
                )
                self.assertEqual(len(registry._processes[key]), 1)
