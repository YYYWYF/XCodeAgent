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

    async def test_end_releases_resources_without_a_record_or_resume_entry(self):
        """结束释放资源且不产生归档；旧 Run 即使保留 checkpoint 也不能继续。"""
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import StateGraph, START, END
        from app.graph.state import ProjectState
        from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
        from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace

        with tempfile.TemporaryDirectory() as directory:
            self.addAsyncCleanup(close_workflow_checkpointer_for_workspace, workspace=directory)
            _write_ready_workbench_lifecycle(directory)
            start_workbench_execution(directory, scope="page", target_id="page1", page_id="page1",
                thread_id="thread", owner_session_id="session", run_id="old", phase="prepare_build_tasks")
            def original_node(state):
                """提供可持久化的原任务现场，验证结束后不会被恢复调用执行。"""
                raise AssertionError("结束后的节点不应执行")

            builder = StateGraph(ProjectState)
            builder.add_node("prepare_build_tasks", original_node)
            builder.add_edge(START, "prepare_build_tasks")
            builder.add_edge("prepare_build_tasks", END)
            graph = builder.compile(checkpointer=InMemorySaver())
            await graph.aupdate_state({"configurable": {"thread_id": "thread"}}, {
                "workspace": directory, "active_run_id": "old", "active_thread_id": "thread",
                "owner_session_id": "session", "phase": "prepare_build_tasks",
            }, as_node="prepare_build_tasks")
            ended = end_workbench_execution(directory, run_id="old")
            self.assertFalse(ended.active_executions)
            self.assertFalse(ended.resource_locks.pages)
            payload = application_lifecycle_payload(ended)
            self.assertNotIn("endedExecutions", payload)
            self.assertNotIn("endedPlanningExecution", payload)
            self.assertNotIn("endedExecutionsCheckedAt", payload)
            frames = "".join([frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload={
                "threadId": "thread", "runId": "new", "messages": [{"role": "user", "content": "继续任务"}],
                "forwardedProps": {"workspaceRoot": directory, "sessionId": "session", "resumeExecutionRunId": "old"},
            })])
            self.assertIn('"type":"RUN_ERROR"', frames)
            self.assertFalse(load_application_lifecycle(directory).active_executions)

    async def test_removed_planning_restart_and_feedback_requests_are_rejected(self):
        """旧的结束后重生成和补充生成请求必须失败，不能保留隐藏续接入口。"""
        from app.protocols.execution_recovery import build_execution_recovery_ag_ui_stream

        with tempfile.TemporaryDirectory() as directory:
            _write_ready_workbench_lifecycle(directory)
            for recovery in (
                {"action": "restart_initial_planning", "sourceRunId": "ended-run"},
                {"action": "continue", "sourceRunId": "ended-run", "feedback": {"request": "补充生成"}},
            ):
                with self.subTest(recovery=recovery):
                    frames = "".join([frame async for frame in build_execution_recovery_ag_ui_stream(payload={
                        "threadId": "thread", "runId": "rejected", "forwardedProps": {
                            "workspaceRoot": directory, "executionRecovery": recovery,
                        },
                    })])
                    self.assertIn('"type":"RUN_ERROR"', frames)
                    self.assertFalse(load_application_lifecycle(directory).active_executions)

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
