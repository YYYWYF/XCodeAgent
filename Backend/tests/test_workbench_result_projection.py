"""验证续接结果按当前会话恢复，读取不会运行 Graph 或再次消费凭据。"""

import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from langgraph.graph import StateGraph

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.execution_recovery import DurableExecutionRecord, DurableExecutionStatus
from app.graph.state import ProjectState
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace, workflow_checkpointer
from app.persistence.execution_recovery import insert_execution, list_executions_for_thread, update_execution_status
from app.protocols.application_lifecycle import ApplicationLifecycleAction, build_application_lifecycle_ag_ui_stream
from app.services.application_lifecycle import create_application_lifecycle, write_application_lifecycle
from app.services.recovery_snapshot import project_recovery_snapshot


def _selection(state):
    """隔离业务执行，只生成待恢复的标准开发目标选择结果。"""
    return {"status": "completed", "phase": "api_design_readiness_gate",
            "message": "已检查 0 个已完成页面/API，你想开发哪个页面或 API？",
            "clarification": {"mode": "development_target_selection", "message": "你想开发哪个页面或 API？"}}


class WorkbenchResultProjectionTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 SQLite checkpoint、执行索引和 AG-UI Get 验证归属及零副作用。"""

    async def asyncSetUp(self):
        """建立已消费并收口的开发 Run，保持生命周期不再持有 active execution。"""
        self.directory = tempfile.TemporaryDirectory()
        self.workspace = self.directory.name
        state = create_application_lifecycle(application_id="app", application_name="结果恢复")
        self.state = state.model_copy(update={"initialization": state.initialization.model_copy(update={
            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            "status": ApplicationLifecycleStatus.COMPLETED})})
        write_application_lifecycle(self.workspace, self.state)
        self.record = DurableExecutionRecord(run_id="run", thread_id="thread", owner_session_id="owner",
            workspace=self.workspace, execution_kind="workbench", first_node="api_design_readiness_gate",
            current_node="api_design_readiness_gate", status=DurableExecutionStatus.COMPLETED,
            started_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        await insert_execution(self.record)
        saver = await workflow_checkpointer(workspace=self.workspace)
        builder = StateGraph(ProjectState)
        builder.add_node("select", _selection)
        builder.set_entry_point("select")
        builder.set_finish_point("select")
        self.graph = builder.compile(checkpointer=saver)
        await self.graph.ainvoke({"workspace": self.workspace, "active_run_id": "run",
            "active_thread_id": "thread", "owner_session_id": "owner", "workflow_scope": "",
            "revision_continuation": {"token": "must-not-be-published"}},
            {"configurable": {"thread_id": "thread"}})

    async def asyncTearDown(self):
        """关闭当前测试工作区的 checkpoint 连接，不影响其他工作区。"""
        await close_workflow_checkpointer_for_workspace(workspace=self.workspace)
        self.directory.cleanup()

    async def test_repeated_get_restores_original_run_without_writes(self):
        """同一会话反复读取返回同 Run，生命周期、checkpoint 和执行数都不变。"""
        path = Path(self.workspace) / ".devagentstudio/application-lifecycle.json"
        before = path.read_bytes()
        config = {"configurable": {"thread_id": "thread"}}
        snapshot = await self.graph.aget_state(config)
        first = await project_recovery_snapshot(workspace=self.workspace, application_id="app", session_id="owner", thread_id="thread")
        self.assertEqual(first["workflow"]["state"]["clarification"]["mode"], "development_target_selection")
        self.assertEqual(first["workflow"]["runId"], "run")
        self.assertNotIn("must-not-be-published", json.dumps(first))
        second = await project_recovery_snapshot(workspace=self.workspace, application_id="app", session_id="owner", thread_id="thread")
        self.assertEqual(first["workflow"], second["workflow"])
        self.assertEqual(first["checkpointId"], second["checkpointId"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((await self.graph.aget_state(config)).config, snapshot.config)
        self.assertEqual(len(await list_executions_for_thread(self.workspace, thread_id="thread")), 1)

    async def test_other_owner_and_newer_run_cannot_restore_old_card(self):
        """错 owner/thread、更新执行和 checkpoint Run 不匹配都不能借旧结果生成卡片。"""
        self.assertIsNone((await project_recovery_snapshot(workspace=self.workspace, application_id="app",
                         session_id="other", thread_id="thread"))["workflow"])
        self.assertIsNone((await project_recovery_snapshot(workspace=self.workspace, application_id="app",
                         session_id="owner", thread_id="other"))["workflow"])
        await insert_execution(self.record.model_copy(update={"run_id": "new-run",
            "started_at": self.record.started_at + timedelta(seconds=1)}))
        self.assertIsNone((await project_recovery_snapshot(workspace=self.workspace, application_id="app",
                         session_id="owner", thread_id="thread"))["workflow"])

    async def test_ended_record_cannot_restore_checkpoint_selection(self):
        """真实执行记录已结束时，旧 checkpoint 的选择和确认字段不再成为可操作入口。"""
        await update_execution_status(workspace=self.workspace, run_id="run", status=DurableExecutionStatus.CANCELLED, ended=True)
        snapshot = await project_recovery_snapshot(workspace=self.workspace, application_id="app", session_id="owner", thread_id="thread")
        self.assertEqual(snapshot["workflow"]["summary"]["status"], "cancelled")
        self.assertFalse(snapshot["workflow"]["summary"].get("clarification"))
        self.assertNotIn("revision_continuation", snapshot["workflow"]["state"])

    async def test_ag_ui_get_includes_result_only_for_requested_session(self):
        """产品读取仍发出完整 AG-UI 生命周期，结果只进入 scoped Get 响应。"""
        stream = "".join([frame async for frame in build_application_lifecycle_ag_ui_stream(payload={
            "threadId": "read-thread", "runId": "read-run", "forwardedProps": {"applicationLifecycle": {
                "action": "get", "workspaceRoot": self.workspace, "sessionId": "owner", "sessionThreadId": "thread"}}})])
        self.assertIn('"type":"RUN_STARTED"', stream)
        self.assertIn('"type":"RUN_FINISHED"', stream)
        self.assertIn('"recoverySnapshot":', stream)
        self.assertIn('"development_target_selection"', stream)
        self.assertNotIn('must-not-be-published', stream)
        self.assertNotIn('recoverySnapshot', (Path(self.workspace) / ".devagentstudio/application-lifecycle.json").read_text())

    def test_get_requires_complete_session_identity(self):
        """请求边界拒绝只有半个会话身份的结果读取。"""
        with self.assertRaises(ValueError):
            ApplicationLifecycleAction(action="get", workspaceRoot=self.workspace, sessionId="owner")
