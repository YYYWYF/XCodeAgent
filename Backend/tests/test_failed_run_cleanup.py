"""验证失败运行的真实退出、资源释放与保留 checkpoint 的重试。"""

import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.domain.application_lifecycle import ExecutionResourceClaim
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    handoff_workbench_execution_for_recovery, load_application_lifecycle,
    start_workbench_execution,
)
from app.protocols.workflow.lifecycle import fail_workflow_lifecycle
from app.services.failed_run_cleanup import (
    drain_failed_run, release_failed_execution_resources, shield_run_cleanup,
)
from app.services.run_workers import bind_run_workers, run_worker
from app.services.workspace_process_registry import workspace_process_registry
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class FailedRunCleanupTests(unittest.IsolatedAsyncioTestCase):
    """使用真实线程、子进程和生命周期文件验证失败释放边界。"""

    def start_execution(self, workspace, run_id="source", target="page-a"):
        """创建包含页面、接口和数据源依赖的工作台运行。"""
        return start_workbench_execution(
            workspace, scope="page", target_id=target, page_id=target,
            thread_id=run_id + "-thread", run_id=run_id, phase="build",
            resource_claims=[
                ExecutionResourceClaim(type="page", targetId=target, role="primary", reason="primary_target"),
                ExecutionResourceClaim(type="api_contract", targetId=target + "-api"),
                ExecutionResourceClaim(type="data_source", targetId=target + "-db"),
            ],
        )

    async def test_waits_for_real_thread_then_releases_only_failed_owner(self):
        """清理未完成时保留锁，线程退出后释放本 Run 且保留全部重试信息。"""
        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            self.start_execution(workspace)
            self.start_execution(workspace, "other", "page-b")
            checkpoint = Path(workspace, "checkpoint-evidence")
            checkpoint.write_bytes(b"checkpoint must survive")
            ready, release = threading.Event(), threading.Event()

            def worker():
                """模拟失败后仍在写盘回滚的同步节点。"""
                with bind_run_workers("source", workspace), run_worker():
                    ready.set()
                    release.wait(5)
                    with self.assertRaises(RuntimeError):
                        with run_worker():
                            self.fail("失败后不能开始下一次工具调用")

            workspace_process_registry.allow_run("source")
            thread = threading.Thread(target=worker)
            thread.start()
            await asyncio.to_thread(ready.wait, 2)
            fail_workflow_lifecycle(workspace, run_id="source", phase="build", error=RuntimeError("test"))
            cleanup = asyncio.create_task(drain_failed_run("source"))
            try:
                await asyncio.sleep(0.05)
                self.assertFalse(cleanup.done())
                with self.assertRaises(ApplicationLifecycleConflictError):
                    release_failed_execution_resources(workspace, "source")
                self.assertEqual(load_application_lifecycle(workspace).resource_locks.pages["page-a"].run_id, "source")
            finally:
                release.set()
                await asyncio.to_thread(thread.join, 2)
                await asyncio.wait_for(cleanup, 3)
            state = release_failed_execution_resources(workspace, "source")
            self.assertNotIn("page-a", state.resource_locks.pages)
            self.assertNotIn("page-a-api", state.resource_locks.api_contracts)
            self.assertNotIn("page-a-db", state.resource_locks.data_sources)
            self.assertEqual(state.resource_locks.pages["page-b"].run_id, "other")
            self.assertEqual(state.active_executions["source"].status.value, "failed")
            self.assertIsNotNone(state.active_executions["source"].error)
            self.assertEqual(checkpoint.read_bytes(), b"checkpoint must survive")
            self.assertEqual(release_failed_execution_resources(workspace, "source").revision, state.revision)

    async def test_kills_real_subprocess_and_blocks_late_process(self):
        """失败清理终止本 Run 子进程，随后启动的命令被取消栅栏拒绝。"""
        with tempfile.TemporaryDirectory() as workspace:
            run_id = "failed-process"
            workspace_process_registry.allow_run(run_id)
            with workspace_process_registry.managed_process(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                workspace=workspace, run_id=run_id,
            ) as process:
                await asyncio.wait_for(drain_failed_run(run_id), 4)
                self.assertIsNotNone(process.poll())
                self.assertFalse(workspace_process_registry.active_run_processes(run_id))
                with self.assertRaises(RuntimeError):
                    with workspace_process_registry.managed_process(
                        [sys.executable, "-c", "pass"], workspace=workspace, run_id=run_id,
                    ):
                        self.fail("失败后不能启动迟到命令")

    async def test_connection_cancellation_does_not_cancel_cleanup(self):
        """调用者取消后后台清理仍然持有资源，直到真实线程退出。"""
        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            self.start_execution(workspace, "shielded")
            fail_workflow_lifecycle(workspace, run_id="shielded", phase="build", error=RuntimeError("test"))
            ready, release, finished = threading.Event(), threading.Event(), asyncio.Event()

            def worker():
                """保留真实工作登记直到允许退出。"""
                with bind_run_workers("shielded", workspace), run_worker():
                    ready.set()
                    release.wait(5)

            async def cleanup():
                """等待线程退出后释放持久化资源。"""
                await drain_failed_run("shielded")
                release_failed_execution_resources(workspace, "shielded")
                finished.set()

            workspace_process_registry.allow_run("shielded")
            thread = threading.Thread(target=worker)
            thread.start()
            await asyncio.to_thread(ready.wait, 2)
            waiter = asyncio.create_task(shield_run_cleanup(cleanup()))
            await asyncio.sleep(0.05)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            try:
                self.assertFalse(finished.is_set())
                self.assertIn("page-a", load_application_lifecycle(workspace).resource_locks.pages)
            finally:
                release.set()
                await asyncio.to_thread(thread.join, 2)
                await asyncio.wait_for(finished.wait(), 3)
            self.assertNotIn("page-a", load_application_lifecycle(workspace).resource_locks.pages)

    async def test_launcher_process_inherits_failed_run(self):
        """启动验收的长生命周期进程也继承当前 Run，失败后可按 Run 回收。"""
        with tempfile.TemporaryDirectory() as workspace:
            run_id = "failed-launcher"
            workspace_process_registry.allow_run(run_id)
            with bind_run_workers(run_id, workspace):
                process = workspace_process_registry.start(workspace, [sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                self.assertIn(process.pid, workspace_process_registry.active_run_processes(run_id))
                await asyncio.wait_for(drain_failed_run(run_id), 4)
                self.assertIsNotNone(process.poll())
            finally:
                workspace_process_registry.release(workspace, process)

    @unittest.skipUnless(os.name == "posix", "验证 POSIX 派生进程组清理")
    async def test_cleans_descendant_after_parent_exits(self):
        """父进程先退出时仍强制清理忽略 SIGTERM 的派生进程。"""
        with tempfile.TemporaryDirectory() as workspace:
            ready = Path(workspace, "child-pid")
            child_code = (
                "import os,signal,time,pathlib; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"pathlib.Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(30)"
            )
            parent_code = f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child_code!r}]); time.sleep(30)"
            run_id = "failed-descendant"
            workspace_process_registry.allow_run(run_id)
            child_pid = None
            with workspace_process_registry.managed_process(
                [sys.executable, "-c", parent_code], workspace=workspace, run_id=run_id,
            ):
                try:
                    for _ in range(100):
                        if ready.exists():
                            break
                        await asyncio.sleep(0.02)
                    self.assertTrue(ready.exists())
                    child_pid = int(ready.read_text())
                    await asyncio.wait_for(drain_failed_run(run_id), 4)
                    for _ in range(100):
                        status = subprocess.run(["ps", "-p", str(child_pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                        if not status or status.startswith("Z"):
                            break
                        await asyncio.sleep(0.02)
                    self.assertTrue(not status or status.startswith("Z"), status)
                finally:
                    if child_pid:
                        try:
                            os.kill(child_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    async def test_retry_reacquires_full_resource_set_and_rejects_new_owner(self):
        """失败释放后重试重新取得完整依赖，冲突时不能覆盖后来任务。"""
        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            self.start_execution(workspace)
            fail_workflow_lifecycle(workspace, run_id="source", phase="build", error=RuntimeError("test"))
            await drain_failed_run("source")
            released = release_failed_execution_resources(workspace, "source")
            expected_keys = released.active_executions["source"].resource_keys
            child = handoff_workbench_execution_for_recovery(
                workspace, source_run_id="source", new_run_id="child", thread_id="source-thread",
                phase="build", expected_lifecycle_revision=released.revision,
            )
            self.assertEqual(child.active_executions["child"].resource_keys, expected_keys)
            self.assertEqual(child.resource_locks.pages["page-a"].run_id, "child")
            self.assertEqual(child.resource_locks.api_contracts["page-a-api"].run_id, "child")
            self.assertEqual(child.resource_locks.data_sources["page-a-db"].run_id, "child")
            fail_workflow_lifecycle(workspace, run_id="child", phase="build", error=RuntimeError("again"))
            await drain_failed_run("child")
            release_failed_execution_resources(workspace, "child")
            self.start_execution(workspace, "competitor")
            before = load_application_lifecycle(workspace)
            with self.assertRaises(ApplicationLifecycleConflictError):
                handoff_workbench_execution_for_recovery(
                    workspace, source_run_id="child", new_run_id="retry", thread_id="source-thread",
                    phase="build", expected_lifecycle_revision=before.revision,
                )
            self.assertEqual(load_application_lifecycle(workspace), before)

    async def test_foreground_timeout_keeps_owner_and_eventually_releases(self):
        """错误回执有界，后台真实退出后自动清锁，其他任务的锁仍保留。"""
        from app.services.failed_run_cleanup import wait_failed_run, require_failed_run_drained
        from app.domain.execution_recovery import RecoveryExecutionError
        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            self.start_execution(workspace, "slow-cleanup")
            self.start_execution(workspace, "other-cleanup", "page-b")
            ready, release = threading.Event(), threading.Event()

            def worker():
                """模拟取消后尚未完成回滚的真实同步线程。"""
                with bind_run_workers("slow-cleanup", workspace), run_worker():
                    ready.set()
                    release.wait(5)

            workspace_process_registry.allow_run("slow-cleanup")
            thread = threading.Thread(target=worker)
            thread.start()
            await asyncio.to_thread(ready.wait, 1)
            fail_workflow_lifecycle(workspace, run_id="slow-cleanup", phase="build", error=RuntimeError("original"))
            finished = asyncio.Event()

            async def cleanup():
                """前台离开后仍持有真正的清理和释放责任。"""
                await drain_failed_run("slow-cleanup")
                release_failed_execution_resources(workspace, "slow-cleanup")
                finished.set()

            try:
                with patch("app.services.failed_run_cleanup.FOREGROUND_CLEANUP_SECONDS", 0.02):
                    self.assertFalse(await wait_failed_run("slow-cleanup"))
                    with self.assertRaises(RecoveryExecutionError) as error:
                        await require_failed_run_drained("slow-cleanup")
                    self.assertEqual(error.exception.code, "RUN_CLEANUP_PENDING")
                    await shield_run_cleanup(cleanup())
                self.assertFalse(finished.is_set())
                self.assertIn("page-a", load_application_lifecycle(workspace).resource_locks.pages)
            finally:
                release.set()
                await asyncio.to_thread(thread.join, 1)
                await asyncio.wait_for(finished.wait(), 2)
            locks = load_application_lifecycle(workspace).resource_locks.pages
            self.assertNotIn("page-a", locks)
            self.assertEqual(locks["page-b"].run_id, "other-cleanup")

    async def test_cancels_before_closing_graph_and_without_business_executor(self):
        """Graph 关闭前已取消命令；没有进程时清理不依赖可能耗尽的业务线程池。"""
        from app.services.failed_run_cleanup import wait_failed_run
        workspace_process_registry.allow_run("close-order")

        async def close():
            """Graph 清理可能依赖命令收到取消信号后才能返回。"""
            self.assertTrue(workspace_process_registry.is_run_cancelled("close-order"))

        with patch("asyncio.to_thread", side_effect=AssertionError("不应使用业务线程池")):
            self.assertTrue(await wait_failed_run("close-order", close()))

    @unittest.skipUnless(os.name == "posix", "验证忽略 SIGTERM 的进程回收")
    async def test_force_kill_progresses_while_graph_close_waits_for_process(self):
        """Graph 关闭等着命令时，独立清理仍能强制回收，不形成相互等待。"""
        from app.services.failed_run_cleanup import wait_failed_run
        with tempfile.TemporaryDirectory() as workspace:
            workspace_process_registry.allow_run("close-process")
            with workspace_process_registry.managed_process(
                [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready',flush=True); time.sleep(30)"],
                workspace=workspace, run_id="close-process", stdout=subprocess.PIPE,
            ) as process:
                self.assertEqual(await asyncio.to_thread(process.stdout.readline), b"ready\n")

                async def close():
                    """模拟关闭 Graph 需要等待该命令退出。"""
                    await asyncio.to_thread(process.wait)

                self.assertTrue(await asyncio.wait_for(wait_failed_run("close-process", close()), 3))
                self.assertIsNotNone(process.poll())

    async def test_runtime_releases_exception_and_business_failure(self):
        """真实 AG-UI runtime 对异常和业务失败都释放锁，并保留精确 Run 失败记录。"""
        from app.protocols.workflow.runtime import build_workflow_ag_ui_stream

        for escaped in (True, False):
            with self.subTest(escaped=escaped), tempfile.TemporaryDirectory() as workspace:
                _write_ready_workbench_lifecycle(workspace)

                class FailedGraph:
                    """以最小 Graph 流触发两类失败收口。"""

                    async def astream(self, state, **kwargs):
                        """在运行开始后抛异常，或正常提交业务失败状态。"""
                        if escaped:
                            raise RuntimeError("escaped failure")
                        yield "updates", {"build": {"phase": "build", "status": "failed", "error": "business failure"}}

                frames = "".join([frame async for frame in build_workflow_ag_ui_stream(
                    graph=FailedGraph(), payload={
                        "threadId": "runtime-thread", "runId": "runtime-run",
                        "messages": [{"role": "user", "content": "开发应用"}],
                        "forwardedProps": {"workspaceRoot": workspace},
                    },
                )])
                state = load_application_lifecycle(workspace)
                self.assertIsNone(state.resource_locks.application)
                self.assertEqual(state.active_executions["runtime-run"].status.value, "failed")
                self.assertIn("RUN_ERROR" if escaped else "RUN_FINISHED", frames)
                self.assertIn('"resourceLocks":{"application":null', frames)
