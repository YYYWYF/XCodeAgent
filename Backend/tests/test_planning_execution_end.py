"""验证设计修订 End 等待真实退出、释放旧修订并允许新的正式修改。"""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.domain.execution_recovery import DurableExecutionRecord
from app.persistence.execution_recovery import insert_execution, get_execution
from app.protocols.application_lifecycle import build_application_lifecycle_ag_ui_stream
from app.protocols.workflow.run_control import WorkflowRunRegistry
from app.services.application_lifecycle import load_application_lifecycle, write_application_lifecycle
from app.services.application_revision_lifecycle import register_revision_impact, submit_revision_impact
from app.services.planning_execution_end import end_planning_execution, stop_planning_execution
from tests.test_design_revision_recovery import setup_revision


class PlanningExecutionEndTests(unittest.IsolatedAsyncioTestCase):
    """使用正式修订服务、真实执行索引与实际 asyncio 任务验证控制链路。"""

    async def prepare(self, workspace):
        """建立用户确认返回设计后正在生成的当前合同。"""
        pending = setup_revision(workspace)
        submit_revision_impact(workspace, interaction_id="impact", decision="approved")
        state = load_application_lifecycle(workspace)
        write_application_lifecycle(workspace, state.model_copy(update={
            "active_run_id": "design-run",
            "active_formal_revision": state.active_formal_revision.model_copy(update={
                "planning_run_id": "design-run", "planning_thread_id": "thread",
            }),
        }))
        now = datetime.now(timezone.utc)
        await insert_execution(DurableExecutionRecord(run_id="design-run", thread_id="thread",
            workspace=workspace, execution_kind="application_planning", workflow_scope="application_planning",
            first_node="requirements", current_node="requirements", status="running", started_at=now, updated_at=now))
        return pending

    async def test_end_waits_and_new_revision_can_start(self):
        """实际任务取消收口后 End 清掉修订，保留文件，后续影响确认可正常登记。"""
        with tempfile.TemporaryDirectory() as workspace:
            pending = await self.prepare(workspace)
            artifact = Path(workspace, "retained-draft.md")
            artifact.write_text("保留已有草稿")
            exited = asyncio.Event()

            async def writer():
                """模拟仍在执行的业务请求，退出标记必须先于结束成功。"""
                try:
                    await asyncio.Event().wait()
                finally:
                    exited.set()

            task = asyncio.create_task(writer())
            self.registry.register("design-run", task, workspace=workspace)
            await asyncio.sleep(0)
            state = await end_planning_execution(workspace, "thread")
            self.assertTrue(exited.is_set())
            self.assertIsNone(state.active_formal_revision)
            self.assertIsNone(state.active_run_id)
            self.assertEqual(state.initialization.stage, "ready_for_workbench")
            self.assertEqual(artifact.read_text(), "保留已有草稿")
            self.assertEqual((await get_execution(workspace, "design-run")).status, "cancelled")
            again = await end_planning_execution(workspace, "thread")
            self.assertEqual(again.revision, state.revision)
            register_revision_impact(workspace, interaction_id="next", source_thread_id="new-thread",
                source_run_id="new-run", request="新的修改", target=pending.target, impact=pending.impact)
            self.assertIsNotNone(submit_revision_impact(workspace, interaction_id="next", decision="approved"))

    async def test_stop_waits_and_keeps_revision_for_original_node(self):
        """停止生成等待真实退出，但不结束修订或清除原任务身份。"""
        with tempfile.TemporaryDirectory() as workspace:
            await self.prepare(workspace)
            before = load_application_lifecycle(workspace)
            exited = asyncio.Event()

            async def writer():
                """模拟在途生成请求，记录其真实退出。"""
                try:
                    await asyncio.Event().wait()
                finally:
                    exited.set()

            task = asyncio.create_task(writer())
            self.registry.register("design-run", task, workspace=workspace)
            await asyncio.sleep(0)
            stopped = await stop_planning_execution(workspace, "thread")
            self.assertTrue(exited.is_set())
            self.assertEqual(stopped.active_formal_revision, before.active_formal_revision)
            self.assertEqual(stopped.active_run_id, "design-run")
            self.assertEqual((await get_execution(workspace, "design-run")).status, "interrupted")

    async def test_stop_timeout_keeps_original_revision(self):
        """退出超时不能变成结束成功或删除继续所需的修订身份。"""
        with tempfile.TemporaryDirectory() as workspace:
            await self.prepare(workspace)
            with patch.object(self.registry, "cancel_and_wait", new=AsyncMock(return_value="cancel_timeout")):
                with self.assertRaisesRegex(RuntimeError, "再次点击停止生成"):
                    await stop_planning_execution(workspace, "thread")
            self.assertIsNotNone(load_application_lifecycle(workspace).active_formal_revision)

    async def test_timeout_retains_ownership_for_end_retry(self):
        """没有真实退出时不假清锁，用户再次结束可沿同一控制入口成功。"""
        with tempfile.TemporaryDirectory() as workspace:
            await self.prepare(workspace)
            with patch.object(self.registry, "cancel_and_wait", new=AsyncMock(return_value="cancel_timeout")):
                with self.assertRaisesRegex(RuntimeError, "再次点击结束任务"):
                    await end_planning_execution(workspace, "thread")
            self.assertIsNotNone(load_application_lifecycle(workspace).active_formal_revision)
            self.assertIsNone((await end_planning_execution(workspace, "thread")).active_formal_revision)

    async def test_ag_ui_end_emits_complete_lifecycle(self):
        """公共动作沿现有 AG-UI 接口发布结果、快照与结束帧。"""
        with tempfile.TemporaryDirectory() as workspace:
            await self.prepare(workspace)
            frames = []
            async for frame in build_application_lifecycle_ag_ui_stream(payload={
                "threadId": "thread", "runId": "end-control", "forwardedProps": {
                    "applicationLifecycle": {"action": "end_planning_execution", "workspaceRoot": workspace}
                }
            }, accept="text/event-stream"):
                frames.extend(json.loads(line[5:]) for line in frame.splitlines() if line.startswith("data:"))
            self.assertEqual(frames[0]["type"], "RUN_STARTED")
            self.assertEqual(frames[-1]["type"], "RUN_FINISHED")
            self.assertIn("STATE_SNAPSHOT", [frame["type"] for frame in frames])
            self.assertIsNone(load_application_lifecycle(workspace).active_formal_revision)
    async def asyncSetUp(self):
        """每个临时工作区使用独立运行登记，避免固定测试 Run 标识互相污染。"""
        self.registry = WorkflowRunRegistry()
        self.registry_patch = patch("app.protocols.workflow.run_control.workflow_run_registry", self.registry)
        self.registry_patch.start()
        self.addCleanup(self.registry_patch.stop)
