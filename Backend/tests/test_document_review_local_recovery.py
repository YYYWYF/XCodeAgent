"""本机现场归属校准与文档审阅配置摘要的正常使用路径回归。"""

from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus, WorkbenchExecutionStatus
from app.domain.execution_recovery import DurableExecutionRecord, DurableExecutionStatus
from app.graph.application_planning_workflow import application_planning_graph_for_request
from app.graph.workflow import workflow_graph_for_request
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.persistence.execution_recovery import get_execution, insert_execution
from app.protocols.application_lifecycle import build_application_lifecycle_ag_ui_stream
from app.services import application_lifecycle as lifecycle_service
from app.services.document_review_assets import find_document_review, require_current_document_review
from app.services.document_review_rebuild import prepare_document_review
from tests.document_review_read_fixture import read_document_review_state
from app.services.session_checkpoint_reconciliation import reconcile_session_checkpoint
from app.workspace.plan_documents import commit_technical_plan_document
from app.workspace.task_documents import write_pending_build_task_plan_atomic, build_task_plan_pending_json_path
from tests.test_pending_build_task_plan_documents import _validated_plan
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class LocalDocumentReviewRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """真实 SQLite、Graph、当前文档及 AG-UI；不会启动模型或下游生成节点。"""

    def setUp(self) -> None:
        """创建隔离的本机工作区与稳定会话身份。"""
        self.h = TechnicalPlanningRecoveryJourneyHarness()
        self.workspace = str(self.h.workspace)
        self.session = "current-local-session"
        self.thread = "current-local-thread"
        self.run = "original-local-run"
        self.config = {"configurable": {"thread_id": self.thread, "checkpoint_ns": ""}}

    async def asyncTearDown(self) -> None:
        """关闭本用例 checkpointer，清理隔离目录。"""
        await close_workflow_checkpointer_for_workspace(workspace=self.workspace)
        self.h.close()

    async def seed_local_checkpoint(self):
        """模拟当前原生 DAG 确认现场仍在，但执行索引和生命周期引用缺失。"""
        commit_technical_plan_document({"workspace": self.workspace},
            {**deepcopy(self.h.candidate), "confirmation_status": "confirmed"})
        lifecycle = lifecycle_service.load_application_lifecycle(self.workspace)
        lifecycle_service.write_application_lifecycle(self.workspace, lifecycle.model_copy(update={
            "initialization": lifecycle.initialization.model_copy(update={
                "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH, "status": ApplicationLifecycleStatus.COMPLETED})}))
        plan = _validated_plan()
        write_pending_build_task_plan_atomic({"workspace": self.workspace}, plan,
            owner_session_id=self.session, planning_run_id="original-planning", workflow_run_id=self.run,
            base_confirmed_plan_digest=None, input_fingerprint="a" * 64,
            build_execution_scope={"type": "application", "targetId": "application"},
            created_at="2026-11-09T00:00:00Z", planning_provenance={"schema_version": "planning-provenance.v2",
                "review_task_ids": list(plan["task_registry"]), "platform_task_ids": [],
                "new_task_ids": list(plan["task_registry"]), "reused_task_ids": []})
        candidate = find_document_review(self.workspace, planning=False)
        graph = await workflow_graph_for_request(workspace=self.workspace, project_id=self.h.project_id)
        await graph.aupdate_state(self.config, {**candidate.state, "active_run_id": self.run,
            "active_thread_id": self.thread, "owner_session_id": self.session}, as_node=candidate.node)
        return graph, await graph.aget_state(self.config)

    async def read(self):
        """按当前会话身份读取只读快照。"""
        return await read_document_review_state(workspace=self.workspace, application_id=self.h.project_id,
            thread_id=self.thread, session_id=self.session)

    async def test_existing_checkpoint_never_proposes_document_rebuild(self) -> None:
        """恢复投影为空也不等于无现场；只读 Get 不扫描文档、不偷偷补索引。"""
        await self.seed_local_checkpoint()
        with patch("app.services.document_review_assets.find_document_review", side_effect=AssertionError("不得进入文档重建")):
            result = await self.read()
        self.assertTrue(result["checkpointAvailable"])
        self.assertIsNone(result["workflow"])
        self.assertIsNone(result["documentReview"])
        self.assertIsNone(await get_execution(self.workspace, self.run))

    async def test_scoped_attach_restores_original_run_and_actionable_confirmation(self) -> None:
        """AG-UI Attach 恢复原索引及确认归属，不改 checkpoint 或 Pending 正文。"""
        graph, before = await self.seed_local_checkpoint()
        pending = build_task_plan_pending_json_path({"workspace": self.workspace})
        original = pending.read_bytes()
        frames = [frame async for frame in build_application_lifecycle_ag_ui_stream(
            payload={"threadId": "attach-control", "runId": "attach-control-run"},
            lifecycle_input={"action": "workspace_attach", "workspaceRoot": self.workspace,
                "sessionId": self.session, "sessionThreadId": self.thread})]
        events = [json.loads(line[6:]) for frame in frames for line in frame.splitlines() if line.startswith("data: ")]
        value = next(event["value"] for event in events if event["type"] == "CUSTOM")
        self.assertEqual(value["status"], "completed", value)
        result = await self.read()
        self.assertEqual(result["workflow"]["runId"], self.run)
        self.assertEqual(result["workflow"]["state"]["clarification"]["mode"], "build_task_plan_confirmation")
        execution = lifecycle_service.load_application_lifecycle(self.workspace).active_executions[self.run]
        self.assertEqual(execution.owner_session_id, self.session)
        self.assertEqual(execution.status, WorkbenchExecutionStatus.AWAITING_USER)
        self.assertIsNotNone(execution.pending_interaction)
        # 真实 resumeReference 读取也必须使用校准后的新交互凭据，不能继续拿 checkpoint 的旧镜像。
        from app.protocols.workflow.resume_source import read_resume_source, WorkflowResumeReference
        from app.protocols.workflow.request import _lifecycle_interaction_submission
        _, values = await read_resume_source(workflow_graph_for_request, {"forwardedProps": {
            "workspaceRoot": self.workspace, "application": {"id": self.h.project_id}, "sessionId": self.session}},
            WorkflowResumeReference(runId=self.run, threadId=self.thread))
        submission = _lifecycle_interaction_submission(values)
        self.assertEqual(submission["id"], execution.pending_interaction.id)
        lifecycle_service._validate_interaction_submission(lifecycle_service.load_application_lifecycle(self.workspace),
            submission=submission, replaces_run_id=self.run, expected_type=execution.pending_interaction.type,
            error_message="恢复后的确认应可用")
        self.assertEqual(pending.read_bytes(), original)
        self.assertEqual((await graph.aget_state(self.config)).config, before.config)
        revision = lifecycle_service.load_application_lifecycle(self.workspace).revision
        self.assertFalse(await reconcile_session_checkpoint(self.workspace, session_id=self.session, thread_id=self.thread))
        self.assertEqual(lifecycle_service.load_application_lifecycle(self.workspace).revision, revision)

    async def test_other_session_cannot_adopt_local_checkpoint(self) -> None:
        """指定别人的会话身份不能补建索引或接管其确认。"""
        graph, before = await self.seed_local_checkpoint()
        self.assertFalse(await reconcile_session_checkpoint(self.workspace, session_id="another-session", thread_id=self.thread))
        self.assertIsNone(await get_execution(self.workspace, self.run))
        self.assertEqual((await graph.aget_state(self.config)).config, before.config)

    async def test_newer_submission_prevents_old_checkpoint_owner_recreation(self) -> None:
        """较新提交尚未写首个 checkpoint 时，校准不能把旧任务重新登记为活动任务。"""
        await self.seed_local_checkpoint()
        at = datetime.now(timezone.utc)
        await insert_execution(DurableExecutionRecord(run_id="newer-run", thread_id=self.thread,
            owner_session_id=self.session, workspace=self.workspace, project_id=self.h.project_id,
            execution_kind="workbench", first_node="prepare_build_tasks", status=DurableExecutionStatus.INTERRUPTED,
            started_at=at, updated_at=at))
        self.assertFalse(await reconcile_session_checkpoint(self.workspace, session_id=self.session, thread_id=self.thread))
        self.assertFalse(lifecycle_service.load_application_lifecycle(self.workspace).active_executions)
        self.assertIsNone((await self.read())["documentReview"])

    async def test_ended_execution_is_not_reopened_by_attach(self) -> None:
        """明确结束后的原现场可以保留，但校准不重新签发确认或恢复占用。"""
        await self.seed_local_checkpoint()
        await reconcile_session_checkpoint(self.workspace, session_id=self.session, thread_id=self.thread)
        lifecycle_service.end_workbench_execution(self.workspace, run_id=self.run, preserve_resume=True)
        self.assertFalse(await reconcile_session_checkpoint(self.workspace, session_id=self.session, thread_id=self.thread))
        self.assertFalse(lifecycle_service.load_application_lifecycle(self.workspace).active_executions)

    async def test_completed_record_cannot_be_reopened_by_an_old_pending_checkpoint(self) -> None:
        """成功终态已经落库时，旧确认 checkpoint 不能重新登记执行或资源占用。"""
        await self.seed_local_checkpoint()
        at = datetime.now(timezone.utc)
        await insert_execution(DurableExecutionRecord(run_id=self.run, thread_id=self.thread,
            owner_session_id=self.session, workspace=self.workspace, project_id=self.h.project_id,
            execution_kind="workbench", first_node="prepare_build_tasks", status=DurableExecutionStatus.COMPLETED,
            started_at=at, updated_at=at))
        self.assertFalse(await reconcile_session_checkpoint(self.workspace, session_id=self.session, thread_id=self.thread))
        self.assertFalse(lifecycle_service.load_application_lifecycle(self.workspace).active_executions)

    async def test_exited_running_record_does_not_block_original_confirmation_resume(self) -> None:
        """进程退出后，旧 running 索引须按真实确认现场收口，实际 resumeReference 才可读取。"""
        await self.seed_local_checkpoint()
        at = datetime.now(timezone.utc)
        await insert_execution(DurableExecutionRecord(run_id=self.run, thread_id=self.thread,
            owner_session_id=self.session, workspace=self.workspace, project_id=self.h.project_id,
            execution_kind="workbench", first_node="prepare_build_tasks", status=DurableExecutionStatus.RUNNING,
            started_at=at, updated_at=at))
        await reconcile_session_checkpoint(self.workspace, session_id=self.session, thread_id=self.thread)
        self.assertEqual((await get_execution(self.workspace, self.run)).status, DurableExecutionStatus.AWAITING_USER)
        from app.protocols.workflow.resume_source import read_resume_source, WorkflowResumeReference
        _, values = await read_resume_source(workflow_graph_for_request, {"forwardedProps": {
            "workspaceRoot": self.workspace, "application": {"id": self.h.project_id}, "sessionId": self.session}},
            WorkflowResumeReference(runId=self.run, threadId=self.thread))
        self.assertEqual(values["active_run_id"], self.run)

    async def test_unrelated_settings_and_json_format_do_not_invalidate_review(self) -> None:
        """仓库地址、名称、分支及 configRevision/序列化变化不撤销已重建的文档审阅。"""
        commit_technical_plan_document({"workspace": self.workspace}, self.h.candidate)
        await prepare_document_review(self.workspace, application_id=self.h.project_id, thread_id=self.h.thread_id)
        graph = await application_planning_graph_for_request(workspace=self.workspace, project_id=self.h.project_id)
        config = {"configurable": {"thread_id": self.h.thread_id}}
        saved = await graph.aget_state(config)
        path = self.h.workspace / ".devagentstudio/application.json"
        value = json.loads(path.read_text())
        value.update(repoUrl="https://example.invalid/another-repository", appName="新的显示名称",
            branchName="another_branch", configRevision=value["configRevision"] + 1)
        path.write_text(json.dumps(value, ensure_ascii=True, sort_keys=True))
        require_current_document_review(saved.values)
        self.assertFalse(await prepare_document_review(self.workspace, application_id=self.h.project_id, thread_id=self.h.thread_id))
        self.assertEqual((await graph.aget_state(config)).config, saved.config)

    async def test_semantic_capability_change_still_invalidates_review(self) -> None:
        """登录能力确实变化时，旧确认不能沿用原能力依据。"""
        commit_technical_plan_document({"workspace": self.workspace}, self.h.candidate)
        await prepare_document_review(self.workspace, application_id=self.h.project_id, thread_id=self.h.thread_id)
        graph = await application_planning_graph_for_request(workspace=self.workspace, project_id=self.h.project_id)
        saved = await graph.aget_state({"configurable": {"thread_id": self.h.thread_id}})
        path = self.h.workspace / ".devagentstudio/application.json"
        value = json.loads(path.read_text())
        value["auth"]["enable"] = True
        value["auth"]["yht"] = {key: "test-only-value" for key in ("clientId", "privateKey", "publicKey", "centerPublicKey")}
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(Exception, "文档或上游已变化"):
            require_current_document_review(saved.values)
