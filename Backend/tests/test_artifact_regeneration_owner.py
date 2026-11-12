"""验证完成首次规划的应用可交接，真实执行与待确认产物仍受保护。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.application_revision import ActiveFormalRevision, PendingRevisionImpact
from app.protocols.application_planning_run_lock import application_planning_run_lock
from app.protocols.workflow.run_control import workflow_run_registry
from app.services.application_lifecycle import (
    application_lifecycle_path, complete_workspace_bootstrap, create_application_lifecycle,
    load_application_lifecycle, start_workbench_execution, write_application_lifecycle,
)
from app.services.artifact_regeneration_handoff import handoff_artifact_generation
from app.services.automatic_artifact_regeneration import prepare_automatic_regeneration


class ArtifactRegenerationOwnerTests(unittest.IsolatedAsyncioTestCase):
    """用正式文件及真实 Bootstrap 完成事务重现旧规划 Run 误拦截。"""

    def ready_workspace(self, workspace):
        """正常完成模板准备，保留生产路径真实留下的上一轮规划归属。"""
        state = create_application_lifecycle(application_id="app", application_name="待开发应用",
            initialization_thread_id=f"planning:{workspace}", active_run_id="finished-planning")
        state = state.model_copy(update={"initialization": state.initialization.model_copy(update={
            "stage": ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
            "status": ApplicationLifecycleStatus.RUNNING,
        })})
        write_application_lifecycle(workspace, state)
        complete_workspace_bootstrap(workspace, succeeded=True, readiness_verified=True)
        root = Path(workspace, ".devagentstudio")
        (root / "application.json").write_text("{}")
        for relative in ("specs/requirement-spec.json", "plans/product-plan.json",
                         "specs/ui-designs.json", "plans/technical-plan.json"):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"confirmation_status": "confirmed"}))
        return load_application_lifecycle(workspace)

    def handoff(self, workspace):
        """进入已有重生成交接，测试不执行模型或消费用户确认。"""
        return handoff_artifact_generation(
            SimpleNamespace(workspace=workspace, run_id="new-work", thread_id="workbench", owner_session_id="session"),
            new_run_id="new-work", node="technical_planning_begin", request="开发天气页",
        )

    async def test_completed_bootstrap_owner_is_preserved_by_development_check(self):
        """首次开发发现失效输入时仍保留完成的规划归属，不创建新的规划任务。"""
        with tempfile.TemporaryDirectory() as workspace:
            before = self.ready_workspace(workspace)
            self.assertEqual(before.active_run_id, "finished-planning")
            root = Path(workspace, ".devagentstudio")
            plan = root / "plans/technical-plan.json"
            plan.write_text(json.dumps({"confirmation_status": "confirmed",
                "basedOn": [{"artifactKey": "product-plan", "sha256": "0" * 64}]}))
            original = {path: path.read_bytes() for path in root.rglob("*.json")
                        if path.resolve() != application_lifecycle_path(workspace)}
            inputs = {"workspace": workspace, "request": "开发天气页", "resume_values": {
                "build_execution_scope": {"type": "page", "targetId": "weather"}}}
            graph = object()
            with patch("app.graph.application_planning_workflow.application_planning_graph_for_request",
                       AsyncMock(return_value=graph)) as planner:
                result = await prepare_automatic_regeneration(inputs, run_id="new-work", thread_id="workbench")
            self.assertIsNone(result)
            planner.assert_not_awaited()
            self.assertNotIn("resume_from", inputs)
            current = load_application_lifecycle(workspace)
            self.assertEqual(current, before)
            self.assertIsNone(current.active_formal_revision)
            for path, content in original.items():
                self.assertEqual(path.read_bytes(), content)

    async def test_complete_documents_do_not_regenerate_or_change_owner(self):
        """全部文档有效时不因旧 Run 镜像重生成或修改已完成应用。"""
        with tempfile.TemporaryDirectory() as workspace:
            self.ready_workspace(workspace)
            path = application_lifecycle_path(workspace)
            before = path.read_bytes()
            result = await prepare_automatic_regeneration(
                {"workspace": workspace, "request": "开发天气页"}, run_id="new-work", thread_id="workbench")
            self.assertIsNone(result)
            self.assertEqual(path.read_bytes(), before)

    async def test_live_previous_graph_is_not_taken_over(self):
        """即使完成状态已写盘，仍存活的原 Graph 也不能被抢占或取消。"""
        with tempfile.TemporaryDirectory() as workspace:
            self.ready_workspace(workspace)
            path = application_lifecycle_path(workspace)
            before = path.read_bytes()
            task = asyncio.create_task(asyncio.Event().wait())
            workflow_run_registry.register("finished-planning", task, workspace=workspace)
            try:
                with self.assertRaisesRegex(ValueError, "其他执行"):
                    self.handoff(workspace)
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(task.cancelled())
                self.assertFalse(task.done())
            finally:
                workflow_run_registry.unregister("finished-planning", task)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_planning_write_window_is_protected(self):
        """同步规划请求尚未退出时不允许接管其产物生成。"""
        with tempfile.TemporaryDirectory() as workspace:
            current = self.ready_workspace(workspace)
            async with application_planning_run_lock(current.initialization.thread_id):
                with self.assertRaisesRegex(ValueError, "其他执行"):
                    self.handoff(workspace)

    async def test_pending_review_and_formal_revision_are_not_taken_over(self):
        """待确认初始化或正式修订即使没有活跃进程，也不能被当作完成镜像释放。"""
        for mode in ("review", "revision", "impact"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as workspace:
                current = self.ready_workspace(workspace)
                if mode == "review":
                    current = current.model_copy(update={"initialization": current.initialization.model_copy(update={
                        "stage": ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
                        "status": ApplicationLifecycleStatus.AWAITING_USER,
                    })})
                elif mode == "revision":
                    current = current.model_copy(update={"active_formal_revision": ActiveFormalRevision(
                        changeId="change", sourceThreadId="planning", sourceRunId="finished-planning",
                        request="修改规划", target={"type": "application"}, impactInteractionId="impact",
                        formalBranch="design_stage_revision", planningThreadId="planning", status="design_planning",
                    )})
                else:
                    current = current.model_copy(update={"pending_revision_impact": PendingRevisionImpact(
                        changeId="change", interactionId="impact", sourceThreadId="planning",
                        sourceRunId="finished-planning", request="修改规划", target={"type": "application"},
                        basedOnLifecycleRevision=current.revision, impact={
                            "formalBranch": "design_stage_revision", "revisionType": "technical_contract_change",
                            "earliestArtifact": "technical-plan", "affectedArtifacts": ["technical-plan"],
                            "affectedResources": [], "reason": "待用户确认影响范围",
                        },
                    )})
                write_application_lifecycle(workspace, current)
                path = application_lifecycle_path(workspace)
                before = path.read_bytes()
                with patch("app.services.artifact_regeneration_handoff.abandon_invalid_pending") as abandon:
                    with self.assertRaisesRegex(ValueError, "其他执行"):
                        self.handoff(workspace)
                abandon.assert_not_called()
                self.assertEqual(path.read_bytes(), before)

    async def test_other_session_work_is_not_taken_over(self):
        """其他会话的开发占用继续受保护，不以进程暂时空闲绕过其确认入口。"""
        with tempfile.TemporaryDirectory() as workspace:
            self.ready_workspace(workspace)
            start_workbench_execution(workspace, scope="application", target_id="application", page_id=None,
                thread_id="other-thread", run_id="other-work", phase="build")
            path = application_lifecycle_path(workspace)
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "其他执行"):
                self.handoff(workspace)
            self.assertEqual(path.read_bytes(), before)
