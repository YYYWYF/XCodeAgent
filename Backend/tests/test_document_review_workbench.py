"""无 checkpoint 的正式修订草稿与任务 DAG 在新会话中重新建立确认归属。"""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.application_revision import ActiveFormalRevision
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.services.application_lifecycle import load_application_lifecycle, write_application_lifecycle
from app.services.document_review_rebuild import prepare_document_review
from tests.document_review_read_fixture import read_document_review_state
from app.services.revision_drafts import create_revision_draft
from app.workspace.plan_documents import commit_technical_plan_document
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class WorkbenchDocumentReviewTests(unittest.IsolatedAsyncioTestCase):
    """真实文档存储、执行登记、checkpoint 与正式确认均不依赖原电脑会话。"""

    def setUp(self) -> None:
        """准备正式规划与旧工作台的修订，B 的路径、线程和会话均不同。"""
        self.h = TechnicalPlanningRecoveryJourneyHarness()
        self.directory = tempfile.TemporaryDirectory()
        self.other = str(Path(self.directory.name) / "user-b")
        commit_technical_plan_document({"workspace": str(self.h.workspace)},
            {**deepcopy(self.h.candidate), "confirmation_status": "confirmed"})
        state = load_application_lifecycle(self.h.workspace)
        write_application_lifecycle(self.h.workspace, state.model_copy(update={"initialization": state.initialization.model_copy(update={
            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH, "status": ApplicationLifecycleStatus.COMPLETED})}))

    async def asyncTearDown(self) -> None:
        """关闭两个工作区的 SQLite 线程，不留下测试执行。"""
        for workspace in (str(self.h.workspace), self.other):
            await close_workflow_checkpointer_for_workspace(workspace=workspace)
        self.h.close()
        self.directory.cleanup()

    def clone(self) -> None:
        """使用 Git 忽略执行数据库，模拟另一个用户拉取资产。"""
        root = self.h.workspace
        (root / ".gitignore").write_text(".devagentstudio/checkpoints/\n.devagentstudio/recovery/\n")
        for command in (["git", "init", "-q", str(root)], ["git", "-C", str(root), "add", "."],
            ["git", "-C", str(root), "-c", "user.name=Review Test", "-c", "user.email=review@example.invalid", "commit", "-qm", "pending assets"],
            ["git", "clone", "-q", str(root), self.other]):
            subprocess.run(command, check=True, capture_output=True)

    async def attach(self) -> None:
        """复用实际打开入口清理失去现场的旧占用。"""
        from app.services.execution_recovery_projection import reconcile_recovery_ownership
        await reconcile_recovery_ownership(self.other)

    async def test_revision_draft_rebuilds_and_can_confirm_existing_content(self) -> None:
        """B 不生成新稿即可审阅并确认 A 的 TechnicalPlan 草稿。"""
        root = self.h.workspace / ".devagentstudio"
        active = ActiveFormalRevision(changeId="cross-user-revision", formalBranch="workbench_plan_revision",
            sourceThreadId="old-thread", sourceRunId="old-run", request="补充技术说明", target={"type": "application"},
            impactInteractionId="old-impact", planningThreadId="old-thread", status="awaiting_user", currentArtifact="technical-plan")
        state = load_application_lifecycle(self.h.workspace)
        write_application_lifecycle(self.h.workspace, state.model_copy(update={"active_formal_revision": active}))
        draft = deepcopy(self.h.candidate)
        draft["architecture"]["frontend"] += " 保留来自另一位用户的修改。"
        create_revision_draft(self.h.workspace, change_id=active.change_id, artifact_key="technical-plan", kind="technical_plan",
            target_id="application", canonical_json_path=root / "plans/technical-plan.json", artifact=draft,
            based_on_paths={"product-plan": root / "plans/product-plan.json"})
        # 旧设计稿即使仍待确认，也不能抢占正式技术修订的恢复入口。
        (root / "specs/ui-designs.json").write_text(json.dumps({
            "confirmation_status": "pending_user_confirmation", "pages": []}))
        self.clone()
        await self.attach()
        artifact_path = Path(self.other) / ".devagentstudio/drafts/revisions/cross-user-revision/technical-plan/artifact.json"
        before = artifact_path.read_bytes()
        self.assertTrue(await prepare_document_review(self.other, application_id=self.h.project_id,
            thread_id="local-thread-b", session_id="local-user-b", planning=False))
        self.assertEqual(artifact_path.read_bytes(), before)
        snapshot = await read_document_review_state(workspace=self.other, application_id=self.h.project_id,
            thread_id="local-thread-b", session_id="local-user-b")
        self.assertTrue(snapshot["checkpointAvailable"])
        self.assertEqual(snapshot["workflow"]["state"]["clarification"]["mode"], "revision_draft_confirmation")
        current = load_application_lifecycle(self.other)
        execution = next(iter(current.active_executions.values()))
        self.assertEqual(execution.owner_session_id, "local-user-b")
        from app.domain.application_revision import RevisionDraftInteraction
        from app.protocols.workflow.revision import bind_revision_draft_interaction
        from app.graph.nodes.application_revision import _handle_revision_interaction
        binding = bind_revision_draft_interaction(self.other, RevisionDraftInteraction(
            changeId=active.change_id, artifactKey="technical-plan", action="confirm",
            interactionId=execution.pending_interaction.id, basedOnLifecycleRevision=execution.pending_interaction.based_on_revision,
            draftSha256=snapshot["workflow"]["state"]["revision_draft"]["draftSha256"]))
        _handle_revision_interaction(Path(self.other), current.active_formal_revision, binding["interaction"],
                                    source_execution_run_id=execution.run_id)
        confirmed = json.loads((Path(self.other) / ".devagentstudio/plans/technical-plan.json").read_text())
        self.assertEqual(confirmed["architecture"]["frontend"], draft["architecture"]["frontend"])
        self.assertEqual(confirmed["confirmation_status"], "confirmed")

    async def test_pending_dag_preserves_tasks_and_rebinds_confirmation_to_new_user(self) -> None:
        """保留 Pending 全部任务，新的确认身份只属于 B，旧用户不能再消费。"""
        from app.workspace.task_documents import write_pending_build_task_plan_atomic, load_pending_build_task_plan
        from tests.test_pending_build_task_plan_documents import _validated_plan
        plan = _validated_plan()
        write_pending_build_task_plan_atomic({"workspace": str(self.h.workspace)}, plan,
            owner_session_id="remote-user-a", planning_run_id="existing-planning", workflow_run_id="remote-run-a",
            base_confirmed_plan_digest=None, input_fingerprint="a" * 64,
            build_execution_scope={"type": "application", "targetId": "application"}, created_at="2026-11-09T00:00:00Z",
            planning_provenance={"schema_version": "planning-provenance.v2", "review_task_ids": list(plan["task_registry"]),
                "platform_task_ids": [], "new_task_ids": list(plan["task_registry"]), "reused_task_ids": []})
        self.clone()
        await self.attach()
        before = load_pending_build_task_plan({"workspace": self.other})
        self.assertTrue(await prepare_document_review(self.other, application_id=self.h.project_id,
            thread_id="local-thread-b", session_id="local-user-b", planning=False))
        after = load_pending_build_task_plan({"workspace": self.other})
        self.assertEqual(after["task_registry"], before["task_registry"])
        self.assertEqual(after["task_graph"], before["task_graph"])
        self.assertEqual(after["confirmation_status"], "pending")
        self.assertEqual(after["draft_identity"]["owner_session_id"], "local-user-b")
        self.assertNotEqual(after["draft_identity"]["workflow_run_id"], "remote-run-a")
        snapshot = await read_document_review_state(workspace=self.other, application_id=self.h.project_id,
            thread_id="local-thread-b", session_id="local-user-b")
        self.assertEqual(snapshot["workflow"]["state"]["clarification"]["mode"], "build_task_plan_confirmation")
        self.assertIsNone(snapshot["documentReview"])
