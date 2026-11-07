"""验证 Backend、attach、worker 崩溃与停止的恢复边界。"""

import asyncio
from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from app.protocols.application_lifecycle import build_application_lifecycle_ag_ui_stream
from app.services.application_lifecycle import create_application_lifecycle, write_application_lifecycle
from app.services import builtin_authorization_orchestrator as runtime
from app.services.builtin_authorization_task_store import AuthorizationTask, read_task, save_task


class AuthorizationTaskLifecycleTests(unittest.TestCase):
    """使用原持久文件和受控执行器，不触发真实 SaaS 或等待计时。"""

    def setUp(self) -> None:
        """建立独立工作区与当前编排身份。"""

        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.generation = uuid4().hex
        save_task(self.root, AuthorizationTask(generation=self.generation, inputFingerprint="a" * 64,
                  status="waiting", updatedAt=runtime._now()))

    def test_worker_crash_is_persisted_without_replay(self) -> None:
        """未预料的 worker 错误转为待核对，不在进程内重试原子动作。"""

        with patch.object(runtime, "tick", side_effect=RuntimeError("private")) as tick:
            runtime._worker(self.root, self.generation, threading.Event(), clock=lambda: 0)
        tick.assert_called_once()
        self.assertEqual(read_task(self.root).failureCategory, "worker_failed")

    def test_stale_worker_cannot_overwrite_replacement(self) -> None:
        """旧 worker 报错时同样检查代次，禁止覆盖接管后的任务。"""

        def replace_then_fail(*args):
            """模拟新任务已经在错误收尾前取得身份。"""

            task = read_task(self.root)
            task.generation = uuid4().hex
            save_task(self.root, task)
            raise RuntimeError("private")

        with patch.object(runtime, "tick", side_effect=replace_then_fail):
            runtime._worker(self.root, self.generation, threading.Event(), clock=lambda: 0)
        self.assertNotEqual(read_task(self.root).generation, self.generation)
        self.assertEqual(read_task(self.root).status, "waiting")

    def test_shutdown_signals_workers_and_waits_executor(self) -> None:
        """有序关闭线程池后，lifespan 可重新开放调度且不扫描文件系统。"""

        stop = threading.Event()
        future = Future()
        executor = Mock()
        with patch.dict(runtime._workers, {str(self.root): (stop, future)}, clear=True), patch.object(runtime, "_executor", executor):
            try:
                runtime.shutdown_builtin_authorization_tasks()
                self.assertTrue(stop.is_set())
                executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
                self.assertFalse(runtime._workers)
            finally:
                runtime.start_builtin_authorization_tasks()

    def test_stop_cancels_queued_worker_without_waiting_for_capacity(self) -> None:
        """排队尚未运行的任务可立即停止，不能等待其他工作区的十分钟查询。"""

        future = Future()
        with patch.dict(runtime._workers, {str(self.root): (threading.Event(), future)}, clear=True):
            try:
                runtime.stop_builtin_authorization_task(str(self.root))
                self.assertTrue(future.cancelled())
                self.assertEqual(read_task(self.root).status, "cancelled")
            finally:
                runtime._blocked.discard(str(self.root))

    def test_workspace_attach_explicitly_resumes_existing_task(self) -> None:
        """通过真实 AG-UI attach 成功生命周期接管，主工作流不会被调用。"""

        lifecycle = create_application_lifecycle(application_id="fixture", application_name="测试应用")
        write_application_lifecycle(self.root, lifecycle)

        async def collect() -> str:
            """完整收集既有工作区 attach 的标准事件。"""

            stream = build_application_lifecycle_ag_ui_stream(payload={"threadId": "attach", "runId": "attach-run",
                "forwardedProps": {"applicationLifecycle": {"action": "workspace_attach", "workspaceRoot": str(self.root)}}})
            return "".join([frame async for frame in stream])

        with patch("app.protocols.application_lifecycle.template_mutation_coordinator.attach_workspace",
                   return_value=SimpleNamespace(action="none", cleaned=False, lifecycle_changed=False)), \
                patch.object(runtime, "resume_builtin_authorization_task", return_value=None) as resume:
            frames = asyncio.run(collect())
        resume.assert_called_once_with(str(self.root))
        self.assertIn('"status":"completed"', frames.replace(" ", ""))
        self.assertIn("RUN_FINISHED", frames)
