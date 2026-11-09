"""通过真实本地 Git 克隆验证：只传资产、不传执行库，也能重新进入原确认门。"""

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace, workflow_checkpoint_db_path
from app.protocols.application_lifecycle import build_application_lifecycle_ag_ui_stream
from app.protocols.application_planning_interrupt import application_planning_interrupt_from_snapshot
from app.services.application_lifecycle import application_lifecycle_path, load_application_lifecycle, write_application_lifecycle
from app.services.document_review_assets import find_document_review, require_current_document_review
from app.services.document_review_rebuild import prepare_document_review
from app.services.recovery_snapshot import project_recovery_snapshot
from app.workspace.plan_documents import commit_technical_plan_document
from app.workspace.spec_documents import write_requirement_spec_draft_document, write_ui_designs_json
from app.workspace.product_plan_documents import write_product_plan_documents
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class DocumentReviewCrossWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    """所有 Graph、SQLite、校验、文档及 AG-UI 动作真实运行；生成节点禁止执行。"""

    def setUp(self) -> None:
        """准备带正式上游的用户 A，以及路径不同且没有运行数据的用户 B。"""
        self.h = TechnicalPlanningRecoveryJourneyHarness()
        self.clone_root = tempfile.TemporaryDirectory()
        self.other = str(Path(self.clone_root.name) / "user-b")

    async def asyncTearDown(self) -> None:
        """关闭两边 SQLite 连接，确保模拟两台电脑不共享存储句柄。"""
        for workspace in (str(self.h.workspace), self.other):
            await close_workflow_checkpointer_for_workspace(workspace=workspace)
        self.h.close()
        self.clone_root.cleanup()

    def seed(self, kind: str) -> None:
        """保存当前待确认版本，保留已有正文与隐藏结构。"""
        state = {"workspace": str(self.h.workspace)}
        if kind == "requirement_document":
            spec = {**deepcopy(self.h.requirement_spec), "confirmation_status": "pending_user_confirmation"}
            plan = {**deepcopy(self.h.product_plan), "confirmation_status": "pending_user_confirmation"}
            write_requirement_spec_draft_document(state, spec)
            write_product_plan_documents(state, plan)
        elif kind == "ui_designs":
            ui = {**deepcopy(self.h.ui_designs), "confirmation_status": "pending_user_confirmation"}
            write_ui_designs_json(state, ui)
        else:
            commit_technical_plan_document(state, self.h.candidate)

    def hashes(self, workspace: str) -> dict[str, str]:
        """核对资产原始字节，运行数据和 lifecycle 归属不参与正文比较。"""
        root = Path(workspace) / ".devagentstudio"
        return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for directory in ("specs", "plans", "drafts") for path in (root / directory).rglob("*") if path.is_file()}

    async def clone_after_source_review(self, kind: str) -> None:
        """A 有真实审阅 checkpoint；B 通过 Git 克隆只取得当前资产及生命周期。"""
        self.seed(kind)
        before = self.hashes(str(self.h.workspace))
        await prepare_document_review(str(self.h.workspace), application_id=self.h.project_id,
                                      thread_id=self.h.thread_id)
        self.assertTrue(workflow_checkpoint_db_path(workspace=str(self.h.workspace)).is_file())
        self.assertEqual(self.hashes(str(self.h.workspace)), before)
        root = self.h.workspace
        (root / ".gitignore").write_text(".devagentstudio/checkpoints/\n.devagentstudio/recovery/\n.devagentstudio/runtime/\n")
        commands = (["git", "init", "-q", str(root)], ["git", "-C", str(root), "add", "."],
            ["git", "-C", str(root), "-c", "user.name=Review Test", "-c", "user.email=review@example.invalid", "commit", "-qm", "pending documents"],
            ["git", "clone", "-q", str(root), self.other])
        for command in commands:
            subprocess.run(command, check=True, capture_output=True)
        self.assertFalse(workflow_checkpoint_db_path(workspace=self.other).exists())
        self.assertEqual(self.hashes(self.other), before)

    async def action(self, workspace: str, action: str, **scope) -> dict:
        """消费真实独立 AG-UI 生命周期，不用手写 HTTP/SSE 作为产品调用。"""
        frames = [frame async for frame in build_application_lifecycle_ag_ui_stream(
            payload={"threadId": "read", "runId": "read-" + action},
            lifecycle_input={"action": action, "workspaceRoot": workspace, **scope})]
        events = [json.loads(line[6:]) for frame in frames for line in frame.splitlines() if line.startswith("data: ")]
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        return next(event["value"] for event in events if event["type"] == "CUSTOM")

    async def test_each_planning_document_rebuilds_original_gate_without_generation(self) -> None:
        """三种主规划文档从 B 的磁盘建立原生确认门，所有文档字节与确认状态不变。"""
        # 各子场景独立克隆，避免第一种草稿遮蔽后续文档的待确认事实。
        for kind in ("requirement_document", "ui_designs", "technical_plan"):
            with self.subTest(kind=kind):
                if kind != "requirement_document":
                    # 独立运行其余场景，复用同一套真实工作区生命周期管理。
                    other = DocumentReviewCrossWorkspaceTests()
                    other.setUp()
                    try:
                        await other.check_planning_kind(kind)
                    finally:
                        await other.asyncTearDown()
                else:
                    await self.check_planning_kind(kind)

    async def check_planning_kind(self, kind: str) -> None:
        """确认重建只执行审阅节点，且 B 的新 Run 不沿用 A 的执行身份。"""
        with patch("app.graph.application_planning_workflow.nodes.requirements", side_effect=AssertionError("不得生成需求")), \
             patch("app.graph.application_planning_workflow.nodes.product_planning", side_effect=AssertionError("不得生成产品规划")), \
             patch("app.graph.application_planning_workflow.nodes.ui_confirmation", side_effect=AssertionError("不得生成UI")), \
             patch("app.graph.application_planning_workflow.technical_planning_generate", side_effect=AssertionError("不得生成技术规划")):
            await self.clone_after_source_review(kind)
            before = self.hashes(self.other)
            result = await self.action(self.other, "get", planningThreadId=self.h.thread_id)
            self.assertFalse(result["lifecycle"]["extensions"]["recoverySnapshot"]["checkpointAvailable"])
            self.assertFalse(workflow_checkpoint_db_path(workspace=self.other).exists())
            result = await self.action(self.other, "prepare_document_review", planningThreadId=self.h.thread_id)
        self.assertEqual(result["status"], "completed", result)
        snapshot = result["lifecycle"]["extensions"]["recoverySnapshot"]
        self.assertEqual(snapshot["workflow"]["state"]["applicationPlanningRecovery"]["classification"], "awaiting_user")
        self.assertNotEqual(load_application_lifecycle(self.other).active_run_id,
                            load_application_lifecycle(str(self.h.workspace)).active_run_id)
        self.assertEqual(self.hashes(self.other), before)
        await close_workflow_checkpointer_for_workspace(workspace=self.other)
        reloaded = await project_recovery_snapshot(workspace=self.other, application_id=self.h.project_id,
            thread_id=self.h.thread_id, planning=True)
        self.assertEqual(reloaded["workflow"]["runId"], snapshot["workflow"]["runId"])
        self.assertEqual(self.hashes(self.other), before)

    async def test_repeated_and_concurrent_prepare_is_idempotent(self) -> None:
        """重复进入和并发请求只建立一份审阅，原文档不会反复落盘。"""
        await self.clone_after_source_review("technical_plan")
        results = await asyncio.gather(*(prepare_document_review(self.other,
            application_id=self.h.project_id, thread_id=self.h.thread_id) for _ in range(3)))
        self.assertEqual(results.count(True), 1)
        from app.persistence.execution_recovery import list_executions_for_thread
        self.assertEqual(len(await list_executions_for_thread(self.other, thread_id=self.h.thread_id)), 1)

    async def test_explicit_confirmation_promotes_original_plan_without_generation(self) -> None:
        """B 只有明确消费当前原生确认门后才落正式版本，内容仍是 A 的原稿。"""
        await self.clone_after_source_review("technical_plan")
        await prepare_document_review(self.other, application_id=self.h.project_id, thread_id=self.h.thread_id)
        from langgraph.types import Command
        from app.graph.application_planning_workflow import application_planning_graph_for_request
        graph = await application_planning_graph_for_request(workspace=self.other, project_id=self.h.project_id)
        config = {"configurable": {"thread_id": self.h.thread_id}}
        saved = await graph.aget_state(config)
        gate = application_planning_interrupt_from_snapshot(saved)
        submission = {key: gate[key] for key in ("gateId", "artifact", "artifactRevision")}
        submission["action"] = "confirm"
        draft = Path(self.other) / ".devagentstudio/drafts/plans/technical-plan.json"
        original = json.loads(draft.read_text())
        with patch("app.graph.application_planning_workflow.technical_planning_generate", side_effect=AssertionError("不得重新生成")):
            await graph.ainvoke(Command(resume=submission), config, interrupt_before=["template_reconcile"])
        formal = Path(self.other) / ".devagentstudio/plans/technical-plan.json"
        self.assertFalse(draft.exists())
        confirmed = json.loads(formal.read_text())
        self.assertEqual(confirmed["confirmation_status"], "confirmed")
        self.assertEqual(confirmed["architecture"], original["architecture"])
        current = await graph.aget_state(config)
        self.assertFalse(current.values.get("document_review"))

    async def test_external_edit_rejects_old_confirmation_then_rebuilds_current_version(self) -> None:
        """外部改文件后旧确认失效，重建使用新正文，不覆盖任何用户编辑。"""
        await self.clone_after_source_review("technical_plan")
        await prepare_document_review(self.other, application_id=self.h.project_id, thread_id=self.h.thread_id)
        from app.graph.application_planning_workflow import application_planning_graph_for_request
        graph = await application_planning_graph_for_request(workspace=self.other, project_id=self.h.project_id)
        saved = await graph.aget_state({"configurable": {"thread_id": self.h.thread_id}})
        old_run = saved.values["active_run_id"]
        path = Path(self.other) / ".devagentstudio/drafts/plans/technical-plan.json"
        value = json.loads(path.read_text())
        value["review_note"] = "另一位用户补充的审阅说明"
        path.write_text(json.dumps(value, ensure_ascii=False))
        with self.assertRaisesRegex(Exception, "文档或上游已变化"):
            require_current_document_review(saved.values)
        await prepare_document_review(self.other, application_id=self.h.project_id, thread_id=self.h.thread_id)
        current = await graph.aget_state({"configurable": {"thread_id": self.h.thread_id}})
        self.assertNotEqual(current.values["active_run_id"], old_run)
        self.assertEqual(current.values["technical_plan"]["review_note"], value["review_note"])
        self.assertIsNotNone(application_planning_interrupt_from_snapshot(current))

    async def test_pending_draft_prevents_old_formal_version_from_marking_ready(self) -> None:
        """旧正式版本已确认也不能让新草稿消失；Attach 仅校准到待确认阶段。"""
        self.seed("technical_plan")
        confirmed = {**deepcopy(self.h.candidate), "confirmation_status": "confirmed"}
        formal = self.h.workspace / ".devagentstudio/plans/technical-plan.json"
        formal.parent.mkdir(parents=True, exist_ok=True)
        formal.write_text(json.dumps(confirmed))
        await self.action(str(self.h.workspace), "workspace_attach")
        state = load_application_lifecycle(str(self.h.workspace))
        self.assertEqual(state.initialization.stage.value, "awaiting_technical_plan_confirmation")
        self.assertFalse(workflow_checkpoint_db_path(workspace=str(self.h.workspace)).exists())

    async def test_incomplete_documents_are_blocked_without_generation(self) -> None:
        """不完整正文不会偷偷生成替代稿或自动确认，错误进入同一恢复结果。"""
        path = self.h.workspace / ".devagentstudio/drafts/plans/technical-plan.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"confirmation_status": "pending_user_confirmation"}))
        result = await self.action(str(self.h.workspace), "get", planningThreadId=self.h.thread_id)
        review = result["lifecycle"]["extensions"]["recoverySnapshot"]["documentReview"]
        self.assertEqual(review["status"], "blocked")
        self.assertFalse(workflow_checkpoint_db_path(workspace=str(self.h.workspace)).exists())

    async def test_corrupted_rebuilt_source_revokes_card_without_transport_failure(self) -> None:
        """重建后正文被外部改坏时撤下旧确认，保留原文件并返回内容诊断。"""
        self.seed("technical_plan")
        workspace = str(self.h.workspace)
        await prepare_document_review(workspace, application_id=self.h.project_id, thread_id=self.h.thread_id)
        draft = self.h.workspace / ".devagentstudio/drafts/plans/technical-plan.json"
        draft.write_text("{外部编辑未完成")
        result = await project_recovery_snapshot(workspace=workspace, application_id=self.h.project_id,
            thread_id=self.h.thread_id, planning=True)
        self.assertIsNone(result["workflow"])
        self.assertEqual(result["documentReview"]["status"], "blocked")
        self.assertEqual(draft.read_text(), "{外部编辑未完成")

    async def test_live_task_prevents_rebuild_before_its_first_checkpoint(self) -> None:
        """运行中的真实任务即使还没写 checkpoint，也不能被新的文档审阅接管。"""
        self.seed("technical_plan")
        workspace = str(self.h.workspace)
        before = self.hashes(workspace)
        from app.protocols.workflow.run_control import workflow_run_registry
        workflow_run_registry.register("live-document-test", asyncio.current_task(), workspace=workspace, thread_id="other-thread")
        try:
            with self.assertRaisesRegex(Exception, "仍有真实任务"):
                await prepare_document_review(workspace, application_id=self.h.project_id, thread_id=self.h.thread_id)
        finally:
            workflow_run_registry.unregister("live-document-test")
        self.assertFalse(workflow_checkpoint_db_path(workspace=workspace).exists())
        self.assertEqual(self.hashes(workspace), before)

    async def test_requirement_revision_consumes_review_binding_before_new_work(self) -> None:
        """用户明确提出修改后进入原设计意图入口，不让旧来源绑定阻断下一版审阅。"""
        self.seed("requirement_document")
        workspace = str(self.h.workspace)
        await prepare_document_review(workspace, application_id=self.h.project_id, thread_id=self.h.thread_id)
        from langgraph.types import Command
        from app.graph.application_planning_workflow import application_planning_graph_for_request
        graph = await application_planning_graph_for_request(workspace=workspace, project_id=self.h.project_id)
        config = {"configurable": {"thread_id": self.h.thread_id}}
        gate = application_planning_interrupt_from_snapshot(await graph.aget_state(config))
        submission = {key: gate[key] for key in ("gateId", "artifact", "artifactRevision")}
        await graph.ainvoke(Command(resume={**submission, "action": "revise", "request": "补充需求说明"}),
            config, interrupt_before=["design_intent_analysis"])
        saved = await graph.aget_state(config)
        self.assertEqual(saved.next, ("design_intent_analysis",))
        self.assertFalse(saved.values.get("document_review"))

    async def test_design_revision_keeps_its_branch_when_user_requests_another_edit(self) -> None:
        """设计正式修订重建后仍走原修订分支，不能误要求工作台专项修订的字段。"""
        self.seed("technical_plan")
        from app.domain.application_revision import ActiveFormalRevision
        workspace = str(self.h.workspace)
        lifecycle = load_application_lifecycle(workspace)
        active = ActiveFormalRevision(changeId="document-review-design", formalBranch="design_stage_revision",
            sourceThreadId="remote-thread", sourceRunId="remote-run", request=self.h.request,
            target={"type": "application"}, impactInteractionId="remote-impact", planningThreadId=self.h.thread_id,
            status="design_planning", currentArtifact="technical-plan", remainingArtifacts=[])
        write_application_lifecycle(workspace, lifecycle.model_copy(update={"active_formal_revision": active}))
        await prepare_document_review(workspace, application_id=self.h.project_id, thread_id=self.h.thread_id)
        from langgraph.types import Command
        from app.graph.application_planning_workflow import application_planning_graph_for_request
        graph = await application_planning_graph_for_request(workspace=workspace, project_id=self.h.project_id)
        config = {"configurable": {"thread_id": self.h.thread_id}}
        gate = application_planning_interrupt_from_snapshot(await graph.aget_state(config))
        submission = {key: gate[key] for key in ("gateId", "artifact", "artifactRevision")}
        await graph.ainvoke(Command(resume={**submission, "action": "revise", "request": "补充技术说明"}),
            config, interrupt_before=["technical_planning_generate"])
        self.assertEqual((await graph.aget_state(config)).next, ("technical_planning_generate",))
        self.assertEqual(load_application_lifecycle(workspace).active_formal_revision.change_id, active.change_id)
