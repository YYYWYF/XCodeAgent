"""验证缺入口释放正式修订与损坏自身产物重新生成的两个逃生出口。"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.services.stage_artifacts import load_stage_artifacts, _GENERATED_BASELINES, _ARTIFACTS
from app.services.generation_baseline import prepare_generation_baseline
from app.services.business_node_retry import release_unretryable_execution
from app.services.application_lifecycle import load_application_lifecycle, write_application_lifecycle
from app.services.application_revision_lifecycle import submit_revision_impact, register_revision_impact
from app.domain.execution_recovery import DurableExecutionRecord
from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from tests.test_design_revision_recovery import setup_revision


class CorruptGenerationBaselineTests(unittest.TestCase):
    """生成只能舍弃自身坏基线，必要上游及确认入口仍保留原错误。"""

    def test_each_generation_can_replace_its_corrupt_output(self):
        """隔离保留坏文件原字节；再次进入节点不再重复读坏文件，并读取新生成内容。"""
        for node, key in _GENERATED_BASELINES.items():
            with self.subTest(node=node), tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace, ".devagentstudio")
                path = root / _ARTIFACTS[key]
                if node in {"requirements", "product_planning"}:
                    path = root / "drafts" / _ARTIFACTS[key]
                path.parent.mkdir(parents=True)
                path.write_bytes(b'{"interrupted":')
                markdown = path.with_suffix(".md")
                markdown.write_text("保留用户编辑")
                for _ in range(2):
                    self.assertEqual(load_stage_artifacts({"workspace": workspace}, node)[key], {})
                backups = list(path.parent.glob(f"{path.name}.corrupt-*.bak"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), b'{"interrupted":')
                self.assertEqual(markdown.read_text(), "保留用户编辑")
                path.write_text('{"new":true}')
                self.assertEqual(load_stage_artifacts({"workspace": workspace}, node)[key], {"new": True})

    def test_task_plan_generation_releases_bad_baseline_but_build_does_not(self):
        """Build 必须消费有效计划；重新生成任务计划才能隔离旧坏计划。"""
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace, ".devagentstudio/plans/build-task-plan.json")
            path.parent.mkdir(parents=True)
            path.write_text("{")
            with self.assertRaises(ValueError):
                load_stage_artifacts({"workspace": workspace}, "build")
            self.assertTrue(path.exists())
            self.assertEqual(load_stage_artifacts({"workspace": workspace}, "prepare_build_tasks")["build_task_plan"], {})
            self.assertFalse(path.exists())

    def test_confirmation_keeps_corrupt_plan_and_io_errors_are_not_discarded(self):
        """确认不能绕过坏产物，权限错误也不能被当成可丢弃基线。"""
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace, ".devagentstudio/plans/technical-plan.json")
            path.parent.mkdir(parents=True)
            path.write_text("{")
            with self.assertRaises(ValueError):
                load_stage_artifacts({"workspace": workspace}, "technical_planning_confirm")
            with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    prepare_generation_baseline(path)
            self.assertTrue(path.exists())


class MissingEntryRevisionReleaseTests(unittest.IsolatedAsyncioTestCase):
    """缺入口释放精确的旧修订，已交接或其他修订不受影响。"""

    def revision(self, workspace):
        """通过正式影响确认创建真实修订，并登记它的规划 Run。"""
        setup_revision(workspace)
        submit_revision_impact(workspace, interaction_id="impact", decision="approved")
        current = load_application_lifecycle(workspace)
        write_application_lifecycle(workspace, current.model_copy(update={"active_run_id": "source"}))
        return load_application_lifecycle(workspace)

    def source(self, workspace):
        """构造属于原规划线程的真实失败记录形状。"""
        return DurableExecutionRecord(run_id="source", thread_id="thread", workspace=workspace,
            execution_kind="application_planning", first_node="requirements", current_node="requirements",
            status="failed", started_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))

    async def test_release_after_run_pointer_already_cleared_allows_new_revision(self):
        """运行已收尾但正式修订残留时也能释放，新会话正式修改可以直接登记。"""
        with tempfile.TemporaryDirectory() as workspace:
            current = self.revision(workspace)
            active = current.active_formal_revision
            self.assertEqual(current.active_formal_revision.planning_run_id, "source")
            write_application_lifecycle(workspace, current.model_copy(update={"active_run_id": None,
                "initialization": current.initialization.model_copy(update={
                    "stage": ApplicationLifecycleStage.ANALYZING_REQUIREMENT, "status": ApplicationLifecycleStatus.RUNNING})}))
            from app.services.conversation_execution import ConversationExecution
            from types import SimpleNamespace
            blocked = ConversationExecution(workspace, "new-session", "not-started", None)
            with self.assertRaisesRegex(ValueError, "尚未完成创建规划"):
                await blocked.start({}, SimpleNamespace(values={}, next=()), None)
            source = self.source(workspace)
            await release_unretryable_execution(source)
            self.assertIsNone(load_application_lifecycle(workspace).active_formal_revision)
            self.assertIsNone(load_application_lifecycle(workspace).active_run_id)
            self.assertEqual(load_application_lifecycle(workspace).initialization.stage, "ready_for_workbench")
            # 不只证明能登记 impact：真实 ConversationExecution.start 也必须能入场。
            execution = ConversationExecution(workspace, "new-session", "new-conversation", None)
            await execution.start({}, SimpleNamespace(values={}, next=()), None)
            await execution.finish({"status": "completed"})
            await execution.close()
            await release_unretryable_execution(source)
            from app.domain.application_revision import RevisionImpact
            pending = register_revision_impact(workspace, interaction_id="new-impact", source_thread_id="new-session",
                source_run_id="new-conversation", request="新会话重新发起需求修改", target=active.target,
                impact=RevisionImpact(formalBranch="design_stage_revision", revisionType="requirement_scope_change",
                    earliestArtifact="requirement-spec", affectedArtifacts=["requirement-spec"],
                    affectedResources=["application"], reason="重新发起"))
            self.assertIsNotNone(pending)
            self.assertIsNotNone(submit_revision_impact(workspace, interaction_id="new-impact", decision="approved"))

    async def test_repeated_design_cleanup_completes_projection_but_initial_creation_does_not(self):
        """修订释放后的重复收尾能对齐初始化投影，首次创建无入口不能伪造已创建完成。"""
        for first_node, expected in (("design_intent_analysis", "ready_for_workbench"),
                                     ("requirements", "analyzing_requirement")):
            with self.subTest(first_node=first_node), tempfile.TemporaryDirectory() as workspace:
                self.revision(workspace)
                current = load_application_lifecycle(workspace)
                write_application_lifecycle(workspace, current.model_copy(update={"active_run_id": None,
                    "active_formal_revision": None, "initialization": current.initialization.model_copy(
                        update={"stage": ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                                "status": ApplicationLifecycleStatus.RUNNING})}))
                await release_unretryable_execution(self.source(workspace).model_copy(update={"first_node": first_node}))
                self.assertEqual(load_application_lifecycle(workspace).initialization.stage, expected)

    async def test_late_old_cleanup_preserves_new_revision_even_on_same_thread(self):
        """同一规划线程的新 Run 已接手，旧失败的延迟清理不能删除新修订。"""
        with tempfile.TemporaryDirectory() as workspace:
            current = self.revision(workspace)
            write_application_lifecycle(workspace, current.model_copy(update={"active_run_id": "new"}))
            current = load_application_lifecycle(workspace)
            write_application_lifecycle(workspace, current.model_copy(update={"active_run_id": None}))
            source = self.source(workspace)
            await release_unretryable_execution(source)
            self.assertEqual(load_application_lifecycle(workspace).active_formal_revision.planning_run_id, "new")
