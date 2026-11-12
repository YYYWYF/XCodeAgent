"""已有资产可独立于执行现场使用；恢复缺失不阻断打开或新的任务登记。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langgraph.graph import StateGraph
from langgraph.types import interrupt

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.graph.state import ProjectState
from app.persistence.checkpoints import (
    close_workflow_checkpointer_for_workspace, workflow_checkpoint_db_path, workflow_checkpointer,
)
from app.protocols.application_lifecycle import build_application_lifecycle_ag_ui_stream
from app.protocols.workflow.run_control import workflow_run_registry
from app.services.application_lifecycle import (
    application_lifecycle_path, create_application_lifecycle, load_application_lifecycle,
    start_workbench_execution, write_application_lifecycle,
)
from tests.document_review_read_fixture import read_planning_review
from app.services.recovery_checkpoint import checkpoint_thread_exists
from tests.document_review_read_fixture import read_document_review_state
from app.services.workspace_checkpoint_reconciliation import reconcile_missing_workspace_checkpoints


def _review(state: dict) -> dict:
    """模拟真实原生确认门，不调用模型或提交产物。"""
    interrupt({"type": "application_planning_review", "artifact": "technical_plan",
               "phase": "technical_planning", "clarification": {"mode": "technical_plan_confirmation"}})
    return {}


class OptionalCheckpointRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """用真实隔离文件、SQLite 与 AG-UI 验证缺失和读取错误的不同语义。"""

    def setUp(self) -> None:
        """创建只有资产和生命周期的应用，不带其他电脑上的执行数据库。"""
        self.directory = tempfile.TemporaryDirectory()
        self.workspace = self.directory.name
        state = create_application_lifecycle(application_id="app", application_name="资产应用",
            initialization_thread_id="planning-thread", active_run_id="old-planning-run")
        self.state = state.model_copy(update={"initialization": state.initialization.model_copy(update={
            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH, "status": ApplicationLifecycleStatus.COMPLETED})})
        write_application_lifecycle(self.workspace, self.state)
        self.assets = {}
        for relative in ("specs/requirement-spec.json", "plans/product-plan.json", "plans/technical-plan.json"):
            path = Path(self.workspace) / ".devagentstudio" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps({"confirmation_status": "confirmed", "marker": relative, "frontend": {"pages": []}})
            path.write_text(content)
            self.assets[path] = content

    async def asyncTearDown(self) -> None:
        """关闭 SQLite 工作线程，再清理本次测试的临时文件。"""
        await close_workflow_checkpointer_for_workspace(workspace=self.workspace)
        self.directory.cleanup()

    async def action(self, action: str, **scope) -> dict:
        """消费真实生命周期 AG-UI 流，只提取其业务结果。"""
        frames = [frame async for frame in build_application_lifecycle_ag_ui_stream(
            payload={"threadId": "inspection", "runId": "inspection-" + action},
            lifecycle_input={"action": action, "workspaceRoot": self.workspace, **scope})]
        events = [json.loads(line[6:]) for frame in frames for line in frame.splitlines() if line.startswith("data: ")]
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        return next(event["value"] for event in events if event["type"] == "CUSTOM")

    def assert_assets_unchanged(self) -> None:
        """资产正文和确认状态不随执行收口改变。"""
        for path, content in self.assets.items():
            self.assertEqual(path.read_text(), content)

    def register_workbench(self, run_id="copied-run", thread_id="copied-thread"):
        """登记一个被复制到新电脑的旧开发占用。"""
        return start_workbench_execution(self.workspace, scope="application", target_id="application",
            page_id=None, thread_id=thread_id, run_id=run_id, phase="prepare_build_tasks", owner_session_id="session")

    async def test_missing_checkpoint_is_empty_recovery_without_creating_database(self) -> None:
        """规划及会话都可缺少现场，不能因此创建空 checkpoint 或制造旧错误卡。"""
        result = await self.action("get", planningThreadId="planning-thread")
        self.assertEqual(result["status"], "completed")
        snapshot = await read_document_review_state(workspace=self.workspace, application_id="app",
            thread_id="planning-thread", planning=True)
        self.assertFalse(snapshot["checkpointAvailable"])
        self.assertIsNone(snapshot["workflow"])
        session = await read_document_review_state(workspace=self.workspace, application_id="app",
            thread_id="copied-thread", session_id="session")
        self.assertFalse(session["checkpointAvailable"])
        self.assertFalse(workflow_checkpoint_db_path(workspace=self.workspace).exists())
        self.assert_assets_unchanged()

    async def test_attach_releases_copied_occupation_and_allows_new_task(self) -> None:
        """没有执行库的新电脑能释放残留锁并登记新任务，资产原样保留。"""
        self.register_workbench()
        result = await self.action("workspace_attach")
        self.assertEqual(result["status"], "completed")
        state = load_application_lifecycle(self.workspace)
        self.assertFalse(state.active_executions)
        self.assertIsNone(state.active_run_id)
        self.assertIsNone(state.resource_locks.application)
        new = self.register_workbench("new-run", "new-thread")
        self.assertEqual(new.resource_locks.application.run_id, "new-run")
        self.assertFalse(workflow_checkpoint_db_path(workspace=self.workspace).exists())
        self.assert_assets_unchanged()

    async def test_existing_database_without_this_thread_is_also_missing(self) -> None:
        """数据库存在但没有当前线程时也不恢复生命周期里的旧任务。"""
        self.register_workbench()
        await workflow_checkpointer(workspace=self.workspace)
        self.assertFalse(await checkpoint_thread_exists(self.workspace, "copied-thread"))
        await reconcile_missing_workspace_checkpoints(self.workspace)
        self.assertFalse(load_application_lifecycle(self.workspace).active_executions)

    async def test_copied_index_cannot_recreate_missing_checkpoint_or_error_card(self) -> None:
        """即使复制了恢复索引，没有现场也只返回空恢复，不建立空库或展示失败卡。"""
        from datetime import datetime, timezone
        from app.domain.execution_recovery import DurableExecutionRecord, DurableExecutionStatus
        from app.persistence.execution_recovery import insert_execution
        now = datetime.now(timezone.utc)
        await insert_execution(DurableExecutionRecord(run_id="old-planning-run", thread_id="planning-thread",
            workspace=self.workspace, project_id="app", owner_session_id="session",
            execution_kind="application_planning", first_node="technical_planning_generate",
            status=DurableExecutionStatus.INTERRUPTED, started_at=now, updated_at=now))
        await self.action("workspace_attach")
        result = await self.action("get", planningThreadId="planning-thread")
        extensions = result["lifecycle"]["extensions"]
        self.assertFalse(await checkpoint_thread_exists(self.workspace, "planning-thread"))
        self.assertEqual(extensions["executionRecovery"]["candidates"], [])
        self.assertFalse(workflow_checkpoint_db_path(workspace=self.workspace).exists())

    async def test_unrecoverable_pending_document_does_not_block_a_new_task(self) -> None:
        """旧 DAG 草稿保留供浏览，但无现场的原执行不再强迫新会话继续旧确认。"""
        from app.workspace.task_documents import write_pending_build_task_plan_atomic, build_task_plan_pending_json_path
        from tests.test_pending_build_task_plan_documents import _validated_plan
        state = {"workspace": self.workspace}
        write_pending_build_task_plan_atomic(state, _validated_plan(), owner_session_id="session",
            planning_run_id="planning", workflow_run_id="copied-run", base_confirmed_plan_digest=None,
            input_fingerprint="a" * 64, build_execution_scope={"type": "application", "targetId": "application"},
            created_at="2026-11-09T00:00:00Z", planning_provenance={
                "schema_version": "planning-provenance.v2",
                "review_task_ids": list(_validated_plan()["task_registry"]), "platform_task_ids": [],
                "new_task_ids": list(_validated_plan()["task_registry"]), "reused_task_ids": [],
            })
        path = build_task_plan_pending_json_path(state)
        before = path.read_bytes()
        self.register_workbench()
        await self.action("workspace_attach")
        new = self.register_workbench("new-run", "new-thread")
        self.assertIn("new-run", new.active_executions)
        self.assertEqual(path.read_bytes(), before)

    async def test_live_work_is_protected_before_first_checkpoint(self) -> None:
        """没有首个 checkpoint 的真实运行仍保留锁，不能借空恢复并发重入。"""
        before = self.register_workbench()
        workflow_run_registry.register("copied-run", asyncio.current_task(), workspace=self.workspace, thread_id="copied-thread")
        try:
            await reconcile_missing_workspace_checkpoints(self.workspace)
            self.assertEqual(load_application_lifecycle(self.workspace), before)
        finally:
            workflow_run_registry.unregister("copied-run")

    async def test_existing_workbench_checkpoint_keeps_occupation_and_run(self) -> None:
        """初始化现场可缺失，但工作台本身有真实确认现场时必须保留执行与资源锁。"""
        before = self.register_workbench()
        builder = StateGraph(ProjectState)
        builder.add_node("review", _review)
        builder.set_entry_point("review")
        builder.set_finish_point("review")
        graph = builder.compile(checkpointer=await workflow_checkpointer(workspace=self.workspace))
        await graph.ainvoke({"workspace": self.workspace, "active_run_id": "copied-run"},
            {"configurable": {"thread_id": "copied-thread"}})
        await reconcile_missing_workspace_checkpoints(self.workspace)
        self.assertEqual(load_application_lifecycle(self.workspace), before)

    async def test_orphan_lock_without_execution_is_released(self) -> None:
        """只有生命周期资源锁而没有执行条目时，也不能阻塞新电脑的新任务。"""
        state = self.register_workbench()
        write_application_lifecycle(self.workspace, state.model_copy(update={"active_executions": {}}))
        await reconcile_missing_workspace_checkpoints(self.workspace)
        self.assertIsNone(load_application_lifecycle(self.workspace).resource_locks.application)
        self.register_workbench("fresh-run", "fresh-thread")

    async def test_live_ui_worker_keeps_planning_occupation(self) -> None:
        """UI 子任务尚未退出时，即使没有父 checkpoint 也保留规划占用。"""
        before = application_lifecycle_path(self.workspace).read_bytes()
        with patch("app.services.ui_design_generation_pool.active_ui_design_count", return_value=1):
            await reconcile_missing_workspace_checkpoints(self.workspace)
        self.assertEqual(application_lifecycle_path(self.workspace).read_bytes(), before)

    async def test_formal_assets_allow_stale_planning_stage_to_enter_workbench(self) -> None:
        """正式文档已经确认时，无现场的旧阶段展示不能挡住新的开发任务。"""
        state = self.state.model_copy(update={"initialization": self.state.initialization.model_copy(update={
            "stage": ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN, "status": ApplicationLifecycleStatus.RUNNING})})
        write_application_lifecycle(self.workspace, state)
        await reconcile_missing_workspace_checkpoints(self.workspace)
        self.assertEqual(load_application_lifecycle(self.workspace).initialization.stage,
                         ApplicationLifecycleStage.READY_FOR_WORKBENCH)
        self.register_workbench("fresh-run", "fresh-thread")
        self.assert_assets_unchanged()

    async def test_unconfirmed_assets_are_never_confirmed_by_attach(self) -> None:
        """缺少现场只跳过恢复，待确认技术稿不能被收口逻辑自动确认。"""
        path = Path(self.workspace) / ".devagentstudio/plans/technical-plan.json"
        content = json.dumps({"confirmation_status": "pending_user_confirmation"})
        path.write_text(content)
        state = self.state.model_copy(update={"initialization": self.state.initialization.model_copy(update={
            "stage": ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
            "status": ApplicationLifecycleStatus.AWAITING_USER})})
        write_application_lifecycle(self.workspace, state)
        await reconcile_missing_workspace_checkpoints(self.workspace)
        self.assertEqual(load_application_lifecycle(self.workspace).initialization.stage,
                         ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION)
        self.assertEqual(path.read_text(), content)

    async def test_stale_lifecycle_run_cannot_reject_current_checkpoint(self) -> None:
        """Run 镜像不一致时仍按当前 checkpoint 恢复原生确认门。"""
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(
            values={"active_run_id": "current-run", "workspace": self.workspace, "phase": "technical_planning",
                    "workflow_scope": "application_planning"}, config={}, next=("review",),
            tasks=(SimpleNamespace(interrupts=(SimpleNamespace(value={"type": "application_planning_review",
                "artifact": "technical_plan", "clarification": {"mode": "technical_plan_confirmation"}}),)),))))
        workflow, _, _ = await read_planning_review(graph=graph, workspace=self.workspace,
            application_id="app", thread_id="planning-thread")
        self.assertEqual(workflow["runId"], "current-run")
        self.assertEqual(workflow["state"]["applicationPlanningRecovery"]["classification"], "awaiting_user")

    async def test_attach_indexes_current_checkpoint_instead_of_lifecycle_run(self) -> None:
        """有真实 checkpoint 但执行索引缺失时只索引当前现场，不补造旧生命周期 Run。"""
        saver = await workflow_checkpointer(workspace=self.workspace)
        builder = StateGraph(ProjectState)
        builder.add_node("review", _review)
        builder.set_entry_point("review")
        builder.set_finish_point("review")
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke({"workspace": self.workspace, "project_id": "app", "active_run_id": "current-run",
            "workflow_scope": "application_planning", "phase": "technical_planning"},
            {"configurable": {"thread_id": "planning-thread"}})
        result = await self.action("workspace_attach")
        self.assertEqual(result["status"], "completed")
        from app.persistence.execution_recovery import get_execution
        self.assertIsNotNone(await get_execution(self.workspace, "current-run"))
        self.assertIsNone(await get_execution(self.workspace, "old-planning-run"))
        self.assert_assets_unchanged()

    async def test_unreadable_checkpoint_preserves_occupation_but_not_block_attach(self) -> None:
        """读取故障不冒充缺失，不解锁；基础资产入口仍能完成 Attach。"""
        self.register_workbench()
        before = application_lifecycle_path(self.workspace).read_bytes()
        with patch("app.services.workspace_checkpoint_reconciliation.checkpoint_thread_exists",
                   AsyncMock(side_effect=OSError("checkpoint temporarily unavailable"))):
            result = await self.action("workspace_attach")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(application_lifecycle_path(self.workspace).read_bytes(), before)
        self.assert_assets_unchanged()
