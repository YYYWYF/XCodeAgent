"""验证结束等待真实工作退出，以及结束后继续的工作区/资源门禁。"""

import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.services.run_workers import bind_run_workers, run_worker, active_worker_runs
from app.services.workspace_process_registry import workspace_process_registry
from app.protocols.workflow.run_control import WorkflowRunRegistry
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError, end_workbench_execution, start_workbench_execution,
    load_application_lifecycle, application_lifecycle_payload,
)
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class EndTaskWorkerTests(unittest.IsolatedAsyncioTestCase):
    """用真实线程验证外层取消后仍不能提前宣告结束。"""

    async def test_cancel_waits_for_detached_thread_and_blocks_next_call(self):
        """工作线程仍在模型/收尾时必须等待，其后的工具调用必须拒绝。"""
        registry = WorkflowRunRegistry()
        started = threading.Event()
        release = threading.Event()
        blocked = threading.Event()

        def work():
            """模拟同步 Agent 的在途调用、文件清理和迟到工具请求。"""
            with run_worker():
                started.set()
                release.wait(2)
                with self.assertRaises(RuntimeError):
                    with run_worker():
                        self.fail("取消后不应进入下一次工具")
                blocked.set()

        async def outer():
            """同步线程可以比被取消的 asyncio Task 活得更久。"""
            with bind_run_workers("worker-test", "/tmp/worker-test"):
                await asyncio.to_thread(work)

        task = asyncio.create_task(outer())
        registry.register("worker-test", task, workspace="/tmp/worker-test")
        await asyncio.to_thread(started.wait, 2)
        cancellation = asyncio.create_task(registry.cancel_and_wait("worker-test", timeout_seconds=2, ending=True))
        await asyncio.sleep(0.05)
        self.assertFalse(cancellation.done())
        self.assertTrue(registry.is_active("worker-test"))
        release.set()
        self.assertEqual(await cancellation, "cancelled")
        self.assertTrue(blocked.is_set())
        self.assertNotIn("worker-test", active_worker_runs())

    async def test_timeout_does_not_claim_success_after_outer_unregister(self):
        """Task 已注销仍需等待同步节点；短超时不能返回 not_running。"""
        registry = WorkflowRunRegistry()
        started, release = threading.Event(), threading.Event()

        def work():
            """保留登记直到真实同步工作退出。"""
            with bind_run_workers("detached-test", "/tmp/detached-test"), run_worker():
                started.set()
                release.wait(2)

        thread = threading.Thread(target=work)
        workspace_process_registry.allow_run("detached-test")
        thread.start()
        await asyncio.to_thread(started.wait, 2)
        try:
            self.assertEqual(await registry.cancel_and_wait("detached-test", timeout_seconds=0.01, ending=True), "cancel_timeout")
        finally:
            release.set()
            await asyncio.to_thread(thread.join, 2)

    async def test_end_resume_restores_confirmation_from_backend_checkpoint(self):
        """真实 Graph 继续只重现确认门，不消费旧提交或信任前端伪造的通过状态。"""
        from langgraph.checkpoint.memory import InMemorySaver
        from app.graph.workflow import build_graph
        from app.protocols.workflow.runtime import build_workflow_ag_ui_stream

        with tempfile.TemporaryDirectory() as directory, patch(
            "app.graph.nodes.code_review.development_review_files", return_value=[],
        ):
            _write_ready_workbench_lifecycle(directory)
            start_workbench_execution(directory, scope="application", target_id="application", page_id=None,
                                      thread_id="thread", owner_session_id="session", run_id="old", phase="review_phase_confirmation")
            graph = build_graph(checkpointer=InMemorySaver())
            await graph.ainvoke({"workspace": directory, "active_run_id": "old", "active_thread_id": "thread",
                                 "quality_gate_passed": True, "resume_from": "review_phase_confirmation",
                                 "build_execution_scope": {"type": "application", "targetId": "application"},
                                 "lifecycle_interaction_submission": {"runId": "stale", "id": "consumed", "basedOnRevision": 1}},
                                {"configurable": {"thread_id": "thread"}})
            end_workbench_execution(directory, run_id="old", preserve_resume=True)
            frames = "".join([frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload={
                "threadId": "thread", "runId": "new", "messages": [{"role": "user", "content": "继续任务"}],
                "forwardedProps": {"workspaceRoot": directory, "sessionId": "session", "resumeExecutionRunId": "old",
                                   "resumeState": {"summary": {"phase": "review_phase_confirmation"},
                                                   "state": {"qualityGatePassed": False, "reviewPhaseConfirmation": {"action": "confirm", "reviewMode": "full"}}}},
            })])
            self.assertNotIn('"type":"RUN_ERROR"', frames)
            current = load_application_lifecycle(directory)
            self.assertFalse(current.ended_executions)
            self.assertEqual(current.active_executions["new"].status, "awaiting_user")
            self.assertEqual(current.active_executions["new"].pending_interaction.type, "review_phase_confirmation")
            # 即使断线前没有保存任何公开消息，只凭后端归档身份仍可继续。
            end_workbench_execution(directory, run_id="new", preserve_resume=True)
            frames = "".join([frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload={
                "threadId": "thread", "runId": "new2", "messages": [{"role": "user", "content": "继续任务"}],
                "forwardedProps": {"workspaceRoot": directory, "sessionId": "session", "resumeExecutionRunId": "new"},
            })])
            self.assertNotIn('"type":"RUN_ERROR"', frames)
            self.assertEqual(load_application_lifecycle(directory).active_executions["new2"].status, "awaiting_user")

    async def test_async_tool_cancellation_waits_for_real_sync_body(self):
        """异步工具内部的同步线程不会因外层取消提前释放登记。"""
        from app.agents.execution_control import RunCancellationMiddleware

        started, release = threading.Event(), threading.Event()
        registry = WorkflowRunRegistry()

        def tool_body():
            """模拟已经开始写文件的同步工具，直到真实收尾才返回。"""
            started.set()
            release.wait(2)

        async def handler(_request):
            """沿常见异步工具适配方式把同步执行交给线程。"""
            return await asyncio.to_thread(tool_body)

        async def agent():
            """在缓存 Agent 中动态读取本轮运行身份。"""
            with bind_run_workers("async-tool-test", "/tmp/async-tool-test"):
                await RunCancellationMiddleware().awrap_tool_call(None, handler)

        task = asyncio.create_task(agent())
        registry.register("async-tool-test", task, workspace="/tmp/async-tool-test")
        await asyncio.to_thread(started.wait, 2)
        cancellation = asyncio.create_task(registry.cancel_and_wait("async-tool-test", timeout_seconds=2, ending=True))
        await asyncio.sleep(0.05)
        self.assertFalse(cancellation.done())
        release.set()
        self.assertEqual(await cancellation, "cancelled")

    async def test_commands_inherit_run_and_other_run_is_not_cancelled(self):
        """命令不必手工传 runId，也必须按 Graph 上下文终止且不影响其他 Run。"""
        import sys

        registry = WorkflowRunRegistry()
        started, release = threading.Event(), threading.Event()
        process_holder = []

        def work():
            """启动真实子进程，确认取消只结束本次登记的命令。"""
            with bind_run_workers("command-test", "/tmp/command-test"), run_worker():
                with workspace_process_registry.managed_process(
                    [sys.executable, "-c", "import time; time.sleep(30)"], workspace="/tmp/command-test",
                ) as process:
                    process_holder.append(process)
                    started.set()
                    process.wait(timeout=5)
                    release.set()

        workspace_process_registry.allow_run("command-test")
        worker = asyncio.create_task(asyncio.to_thread(work))
        await asyncio.to_thread(started.wait, 2)
        self.assertEqual(await registry.cancel_and_wait("command-test", workspace="/tmp/unrelated-workspace", ending=True), "cancel_timeout")
        self.assertIsNone(process_holder[0].poll())
        with bind_run_workers("other-test", "/tmp/command-test"), run_worker():
            self.assertEqual(await registry.cancel_and_wait("command-test", timeout_seconds=2, ending=True), "cancelled")
            self.assertIn("other-test", active_worker_runs())
        await worker
        self.assertTrue(release.is_set())
        self.assertIsNotNone(process_holder[0].poll())


class EndedTaskResumeTests(unittest.TestCase):
    """验证继续资格随真实代码变化失效，且资源锁原子重新获取。"""

    def _end(self, directory):
        """创建一个有源码、配置及正式产物的已结束任务。"""
        _write_ready_workbench_lifecycle(directory)
        root = Path(directory)
        (root / "app.ts").write_text("original")
        (root / ".devagentstudio/application.json").write_text("{}")
        start_workbench_execution(directory, scope="page", target_id="page1", page_id="page1",
                                  thread_id="thread", owner_session_id="session", run_id="old", phase="build")
        return end_workbench_execution(directory, run_id="old", preserve_resume=True)

    def _resume(self, directory, **overrides):
        """从原身份继续，允许测试替换目标或 owner 进行越权验证。"""
        arguments = dict(scope="page", target_id="page1", page_id="page1", thread_id="thread",
                         owner_session_id="session", run_id="new", phase="build", replaces_run_id="old")
        return start_workbench_execution(directory, **{**arguments, **overrides})

    def test_unchanged_releases_then_reacquires_resources(self):
        """只保留继续资格，结束时释放锁，继续成功时消费归档并重建锁。"""
        with tempfile.TemporaryDirectory() as directory:
            ended = self._end(directory)
            self.assertFalse(ended.active_executions)
            self.assertFalse(ended.resource_locks.pages)
            self.assertTrue(application_lifecycle_payload(ended)["endedExecutions"]["old"]["workspaceUnchanged"])
            runtime = Path(directory) / ".devagentstudio/checkpoints"
            runtime.mkdir()
            (runtime / "run.sqlite").write_bytes(b"changed runtime")
            resumed = self._resume(directory)
            self.assertNotIn("old", resumed.ended_executions)
            self.assertEqual(resumed.resource_locks.pages["page1"].run_id, "new")

    def test_changed_code_or_configuration_rejects_resume_and_preserves_archive(self):
        """源码、配置、增加和删除文件都不能以旧状态继续。"""
        for name in ("app.ts", ".devagentstudio/application.json", "new.ts"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                ended = self._end(directory)
                (Path(directory) / name).write_text("changed")
                self.assertFalse(application_lifecycle_payload(ended)["endedExecutions"]["old"]["workspaceUnchanged"])
                with self.assertRaisesRegex(ApplicationLifecycleConflictError, "工作区已变化"):
                    self._resume(directory)
                self.assertIn("old", load_application_lifecycle(directory).ended_executions)

    def test_change_after_eligible_projection_rejects_resume_at_admission(self):
        """GET 曾允许继续也不能授权后续变更：入场必须重新检查磁盘并保留归档。"""
        with tempfile.TemporaryDirectory() as directory:
            ended = self._end(directory)
            eligible = application_lifecycle_payload(ended)
            self.assertTrue(eligible["endedExecutions"]["old"]["workspaceUnchanged"])
            # 模拟按钮已显示、资格 GET 已返回后，另一会话才改动源码。
            (Path(directory) / "app.ts").write_text("changed after eligibility GET")
            with self.assertRaisesRegex(ApplicationLifecycleConflictError, "工作区已变化"):
                self._resume(directory)
            current = load_application_lifecycle(directory)
            self.assertFalse(current.active_executions)
            self.assertFalse(current.resource_locks.pages)
            self.assertIn("old", current.ended_executions)

    def test_other_session_and_resource_owner_reject_resume(self):
        """代码未变也不能串会话或抢占另一任务的资源。"""
        with tempfile.TemporaryDirectory() as directory:
            self._end(directory)
            with self.assertRaises(ApplicationLifecycleConflictError):
                self._resume(directory, owner_session_id="another")
            start_workbench_execution(directory, scope="page", target_id="page1", page_id="page1",
                                      thread_id="other", owner_session_id="other", run_id="other", phase="build")
            with self.assertRaises(ApplicationLifecycleConflictError):
                self._resume(directory)
            self.assertIn("old", load_application_lifecycle(directory).ended_executions)
