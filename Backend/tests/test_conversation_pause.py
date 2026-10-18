"""二次修改真实线程暂停和 checkpoint 原生续跑回归。"""
import asyncio
import tempfile
import threading
import unittest
from unittest.mock import patch
from langgraph.checkpoint.memory import InMemorySaver
from app.domain.application_lifecycle import ApplicationInitialization
from app.graph.direct_modification_workflow import build_direct_modification_graph
from app.persistence.execution_recovery import get_execution
from app.protocols.direct_modification import build_conversation_ag_ui_stream
from app.protocols.workflow.run_control import workflow_run_registry
from app.services.application_lifecycle import create_application_lifecycle, write_application_lifecycle, load_application_lifecycle
from app.services.run_workers import active_worker_runs


class ConversationPauseTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 Graph 和取消控制，不修改 checkpoint。"""

    async def test_pause_drains_worker_and_resumes_checkpoint(self):
        """退出前持续等待；继续只重进未完成节点，原确认门仍保留。"""
        await self._run_pause_scenario()

    async def test_failure_after_pause_continuation_uses_native_recovery(self):
        """主动继续的新 Run 若再次异常，仍由兜底恢复原节点。"""
        await self._run_pause_scenario(fail_on_resume=True)

    async def _run_pause_scenario(self, fail_on_resume=False):
        """驱动真实暂停、原生继续及可选的后续异常恢复。"""
        entered, release = threading.Event(), threading.Event()
        calls = {"scan": 0, "classify": 0}

        def scan(state):
            """记录已完成扫描，识别是否重启整个 Graph。"""
            calls["scan"] += 1
            return {"status": "completed", "phase": "scan_workspace_code"}

        def classify(state):
            """首次等待取消，续跑必须仍使用原始业务要求。"""
            calls["classify"] += 1
            if calls["classify"] == 1:
                entered.set()
                release.wait(5)
            if fail_on_resume and calls["classify"] == 2:
                raise RuntimeError("继续后的模型请求异常")
            self.assertEqual(state["request"], "修改按钮文字")
            return {"phase": "classify_intent", "status": "requires_user_input",
                    "clarification": {"mode": "implementation_fix_confirmation", "message": "请确认修改"}}

        async def finalize(state):
            """保存正常确认边界，不执行下游写入。"""
            return {}

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(application_id="app", application_name="暂停验证")
            write_application_lifecycle(directory, lifecycle.model_copy(update={"initialization": ApplicationInitialization(
                stage="ready_for_workbench", status="completed", threadId="planning")}))
            with (patch("app.graph.direct_modification_workflow.scan_workspace_code", scan),
                  patch("app.graph.direct_modification_workflow.classify_direct_modification", classify),
                  patch("app.graph.direct_modification_workflow.finalize_direct_modification", finalize)):
                graph = build_direct_modification_graph(checkpointer=InMemorySaver())

            async def factory(**kwargs):
                """仅替换 Graph 构造，持久化和控制面使用真实实现。"""
                return graph

            async def run(run_id, resume=None, owner="session"):
                """消费标准 AG-UI 请求，只提交来源运行身份。"""
                conversation = {"workspaceRoot": directory}
                if resume:
                    conversation["resumeExecutionRunId"] = resume
                return "".join([frame async for frame in build_conversation_ag_ui_stream(payload={
                    "threadId": "thread", "runId": run_id,
                    "messages": [{"role": "user", "content": "继续按钮文案" if resume else "修改按钮文字"}],
                    "forwardedProps": {"sessionId": owner, "conversation": conversation}})])

            with (patch("app.protocols.direct_modification.direct_modification_graph_for_request", factory),
                  patch("app.graph.direct_modification_workflow.direct_modification_graph_for_request", factory)):
                task = asyncio.create_task(run("paused"))
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                self.assertIn("paused", active_worker_runs())
                stop = asyncio.create_task(workflow_run_registry.cancel_and_wait("paused", workspace=directory))
                try:
                    async def wait_stopping():
                        """等待控制面的实际停止投影，避免磁盘调度超过固定 100ms 造成误报。"""
                        while load_application_lifecycle(directory).active_executions["paused"].status.value != "stopping":
                            await asyncio.sleep(.01)

                    await asyncio.wait_for(wait_stopping(), timeout=3)
                    self.assertFalse(stop.done())
                    self.assertEqual(load_application_lifecycle(directory).active_executions["paused"].status.value, "stopping")
                    self.assertEqual((await get_execution(directory, "paused")).status.value, "interrupted")
                    self.assertIn("不是可继续的主动暂停", await run("too-early", "paused"))
                finally:
                    release.set()
                self.assertEqual(await stop, "cancelled")
                await asyncio.gather(task, return_exceptions=True)
                self.assertNotIn("paused", active_worker_runs())
                self.assertEqual((await get_execution(directory, "paused")).status.value, "cancelled")
                before = await graph.aget_state({"configurable": {"thread_id": "thread"}})
                self.assertEqual(before.next, ("classify_intent",))
                self.assertIn("二次修改运行不属于当前会话", await run("wrong-owner", "paused", "other"))
                self.assertEqual(calls, {"scan": 1, "classify": 1})
                resumed = await run("continued", "paused")
                expected_run = "continued"
                if fail_on_resume:
                    from app.services.execution_recovery_projection import resolve_execution_recovery_projection
                    from app.protocols.execution_recovery import build_execution_recovery_ag_ui_stream
                    self.assertEqual((await get_execution(directory, "continued")).status.value, "failed")
                    projection = await resolve_execution_recovery_projection(directory, active_workbench_run_ids={"continued"})
                    self.assertEqual(len(projection.candidates), 1)
                    plan = projection.candidates[0].recovery_action_plan
                    self.assertEqual(plan.status.value, "recoverable")
                    recovered = "".join([frame async for frame in build_execution_recovery_ag_ui_stream(payload={
                        "forwardedProps": {"workspaceRoot": directory, "executionRecovery": {
                            "action": "execute", "incidentId": plan.incident_id,
                            "actionId": plan.primary_action.action_id}}})])
                    self.assertNotIn('"type":"RUN_ERROR"', recovered, recovered[-4000:])
                    expected_run = next(iter(load_application_lifecycle(directory).active_executions))
                else:
                    self.assertNotIn('"status":"failed"', resumed, resumed[-4000:])
                self.assertEqual(calls, {"scan": 1, "classify": 3 if fail_on_resume else 2})

                after = await graph.aget_state({"configurable": {"thread_id": "thread"}})
                self.assertEqual(after.values["active_run_id"], expected_run)
                self.assertEqual(after.values["request"], "修改按钮文字")
                self.assertEqual(after.values["clarification"]["mode"], "implementation_fix_confirmation")
                self.assertFalse(after.next)
                self.assertEqual(load_application_lifecycle(directory).active_executions[expected_run].status.value, "awaiting_user")
                self.assertIn("不是可继续的主动暂停", await run("duplicate", "paused"))
                self.assertEqual(calls, {"scan": 1, "classify": 3 if fail_on_resume else 2})

    def test_small_task_inherits_run_cancellation_fence(self):
        """无 packet executionRunId 的二次修改也必须阻断取消后的文件写入。"""
        from types import SimpleNamespace
        from app.agents.small_task.runner import invoke_small_task_agent
        from app.agents.small_task.scope import is_small_task_path_allowed
        from app.services.run_workers import bind_run_workers
        from app.services.workspace_process_registry import workspace_process_registry

        def invoke(*args, **kwargs):
            """模拟迟到的真实 Agent 工具检查，不读取或改动测试文件。"""
            return is_small_task_path_allowed("frontend/src/Page.tsx")

        run_id = "conversation-cancelled-tools"
        workspace_process_registry.cancel_run(run_id)
        try:
            with (bind_run_workers(run_id, "/tmp"),
                  patch("app.agents.create_agent_bundle", return_value=SimpleNamespace(small_task=object())),
                  patch("app.agents.small_task.runner.invoke_agent_with_tool_activity", invoke)):
                with self.assertRaisesRegex(RuntimeError, "停止"):
                    invoke_small_task_agent(packet={"allowedPaths": ["frontend/src/**"]}, workspace="/tmp")
        finally:
            workspace_process_registry.allow_run(run_id)
