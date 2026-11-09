"""首次三阶段停止后用新意见重生成，真实 Graph、SQLite 和确认门回归。"""

from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.application_revision import ActiveFormalRevision, RevisionTarget
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.persistence.execution_recovery import get_execution
from app.protocols.application_planning_interrupt import application_planning_interrupt_from_snapshot
from app.protocols.execution_recovery import build_execution_recovery_ag_ui_stream, _parse_request
from app.services.application_lifecycle import create_application_lifecycle, load_application_lifecycle, write_application_lifecycle
from app.services.execution_recovery import observe_execution_started
from app.services.planning_execution_end import end_planning_execution
from app.services.product_plan import create_product_plan
from app.workspace.product_plan_documents import write_confirmed_product_plan_documents
from app.workspace.spec_documents import write_confirmed_requirement_spec_document, write_ui_designs_json
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class InitialPlanningFeedbackTests(unittest.IsolatedAsyncioTestCase):
    """仅替换模型或页面生成边界，其余使用生产链路。"""

    async def asyncSetUp(self):
        """使用全新子工作区，确保首次阶段没有该阶段已生成的文件。"""
        self.h = TechnicalPlanningRecoveryJourneyHarness()
        self.formal = False
        original = self.h.workspace
        self.h.workspace = original / "initial"
        config = self.h.workspace / ".devagentstudio/application.json"
        config.parent.mkdir(parents=True)
        config.write_text((original / ".devagentstudio/application.json").read_text())
        self.settings = patch("app.config.Settings.from_env", side_effect=self.h.current_settings)
        self.settings.start()

    async def asyncTearDown(self):
        """关闭工作区持久化连接并移除本测试的临时目录。"""
        await close_workflow_checkpointer_for_workspace(workspace=str(self.h.workspace))
        self.settings.stop()
        self.h.close()

    async def stopped(self, stage, node):
        """在首次生成入口建立真实 checkpoint，并通过生产 End 保存取消凭据。"""
        state = {"workspace": str(self.h.workspace)}
        if node != "requirements":
            write_confirmed_requirement_spec_document(state, self.h.requirement_spec)
            write_confirmed_product_plan_documents(state, self.h.product_plan)
        if node == "technical_planning_begin":
            write_ui_designs_json(state, self.h.ui_designs)
        lifecycle = create_application_lifecycle(application_id=self.h.project_id, application_name="天气预报应用",
            initialization_thread_id=self.h.thread_id, active_run_id=self.h.source_run_id)
        lifecycle.initialization.stage = stage
        lifecycle.initialization.status = ApplicationLifecycleStatus.RUNNING
        if self.formal:
            technical = node == "technical_planning_begin"
            lifecycle.active_formal_revision = ActiveFormalRevision(
                changeId="feedback-revision", formalBranch="workbench_plan_revision" if technical else "design_stage_revision",
                sourceThreadId="development-thread", sourceRunId="development-run", request=self.h.request,
                target=RevisionTarget(type="application"), impactInteractionId="approved-impact",
                planningThreadId=self.h.thread_id, planningRunId=self.h.source_run_id,
                status="drafting" if technical else "design_planning",
                currentArtifact={"requirements": "requirement-spec", "product_planning": "product-plan",
                    "ui_confirmation": "ui-design", "technical_planning_begin": "technical-plan"}[node],
            )
        write_application_lifecycle(self.h.workspace, lifecycle)
        await self.h.initialize_graph()
        await self.h.graph.aupdate_state({"configurable": {"thread_id": self.h.thread_id}}, {
            "phase": node, "resume_from": node, "requirement_spec": {}, "product_plan": {}, "ui_designs": {},
        }, as_node="workflow_entry")
        await observe_execution_started(workspace=str(self.h.workspace), project_id=self.h.project_id,
            thread_id=self.h.thread_id, run_id=self.h.source_run_id, workflow_scope="application_planning", first_node=node)
        await end_planning_execution(str(self.h.workspace), self.h.thread_id)
        self.assertEqual(load_application_lifecycle(self.h.workspace).initialization.status,
            "completed" if self.formal else "cancelled")

    async def send(self, request="增加补充说明", **fields):
        """通过正式 AG-UI 恢复动作提交意见，保留完整生命周期与错误供断言。"""
        with patch("app.protocols.execution_recovery.recovery_graph_for_execution", AsyncMock(return_value=self.h.graph)):
            frames = [frame async for frame in build_execution_recovery_ag_ui_stream(payload={
                "threadId": self.h.thread_id,
                "forwardedProps": {"workspaceRoot": str(self.h.workspace), "executionRecovery": {
                    "action": "continue" if self.formal else "restart_initial_planning", "sourceRunId": self.h.source_run_id,
                    "feedback": {"request": request, **fields},
                }},
            })]
        return [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]

    async def assert_review(self, events, artifact):
        """新版本必须挂起在当前确认门，原执行保持取消、归属交给新 Run。"""
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertFalse([event for event in events if event["type"] == "RUN_ERROR"], events)
        snapshot = await self.h.graph.aget_state({"configurable": {"thread_id": self.h.thread_id}})
        self.assertEqual(application_planning_interrupt_from_snapshot(snapshot)["artifact"], artifact)
        current = load_application_lifecycle(self.h.workspace)
        self.assertEqual(current.initialization.status, "awaiting_user")
        self.assertIsNone(current.ended_planning_execution)
        if self.formal:
            self.assertEqual(current.active_formal_revision.change_id, "feedback-revision")
            self.assertEqual(current.active_formal_revision.source_thread_id, "development-thread")
            self.assertEqual(current.active_formal_revision.planning_run_id, current.active_run_id)
        else:
            self.assertIsNone(current.active_formal_revision)
        self.assertNotEqual(current.active_run_id, self.h.source_run_id)
        source = await get_execution(self.h.workspace, self.h.source_run_id)
        self.assertEqual(source.status, "cancelled")
        self.assertIsNotNone(source.ended_at)
        return snapshot

    async def test_requirement_without_baseline_preserves_original_and_waits_for_joint_confirmation(self):
        """首次需求还未产生草稿时，新意见保留原场景，需求与产品仍联合确认。"""
        await self.verify_requirement_feedback(ApplicationLifecycleStage.ANALYZING_REQUIREMENT, "requirements")

    async def test_stopped_product_generation_returns_to_requirement_and_joint_confirmation(self):
        """联合文档的产品生成中停止后，补充意见先更新需求，再生成产品草稿。"""
        await self.verify_requirement_feedback(ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT, "product_planning")

    async def verify_requirement_feedback(self, stage, node):
        """共用首次分析与联合文档生成的补充校验，验证原需求和新意见都进入生成器。"""
        await self.stopped(stage, node)
        spec = deepcopy(self.h.requirement_spec)
        spec["confirmation_status"] = "pending_user_confirmation"
        with patch("app.graph.nodes.requirements.analyze_requirements_with_chat_model", return_value={
            "requirement_spec": spec, "clarification": {"status": "clear", "questions": []},
        }) as model, patch("app.graph.nodes.product_planning.plan_product_with_chat_model",
                side_effect=lambda current, **_kwargs: create_product_plan(current)):
            events = await self.send("增加逐日天气说明")
        snapshot = await self.assert_review(events, "requirement_document")
        self.assertIn(self.h.request, model.call_args.args[0])
        self.assertIn("增加逐日天气说明", model.call_args.args[0])
        self.assertFalse(snapshot.values.get("requirements_confirmed"))
        self.assertEqual(snapshot.values["product_plan"]["confirmation_status"], "pending_user_confirmation")
        with patch("app.graph.nodes.requirements.analyze_requirements_with_chat_model") as repeated:
            duplicate = await self.send("重复提交")
        repeated.assert_not_called()
        self.assertTrue(any(event["type"] == "RUN_ERROR" for event in duplicate))

    async def test_ui_without_baseline_uses_original_adjustment_and_waits_for_confirmation(self):
        """UI 尚无完整稿时，补充正文进入原 UI 动作，不能自动进入技术规划。"""
        await self.stopped(ApplicationLifecycleStage.GENERATING_UI_DESIGNS, "ui_confirmation")
        manifest = {**self.h.ui_designs, "confirmation_status": "pending_user_confirmation"}
        with patch("app.graph.nodes.ui_confirmation._apply_ui_design_action", AsyncMock(return_value=(manifest, None))) as adjust:
            events = await self.send("卡片改为紧凑布局")
        snapshot = await self.assert_review(events, "ui_designs")
        self.assertEqual("卡片改为紧凑布局", adjust.call_args.args[2]["instruction"])
        self.assertEqual(snapshot.next, ("ui_confirmation_review",))
        self.assertNotEqual(load_application_lifecycle(self.h.workspace).initialization.stage, "generating_technical_plan")

    async def test_technical_without_baseline_regenerates_and_waits_for_confirmation(self):
        """技术规划尚无草稿时仍使用原生成器，候选提交后重新等待用户确认。"""
        await self.stopped(ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN, "technical_planning_begin")
        with patch("app.graph.nodes.planning._generate_valid_technical_plan", return_value=(deepcopy(self.h.candidate), [], None)) as model:
            events = await self.send("补充天气趋势说明")
        snapshot = await self.assert_review(events, "technical_plan")
        self.assertIn(self.h.request, model.call_args.args[0]["request"])
        self.assertIn("补充天气趋势说明", model.call_args.args[1]["planning_adjustment_request"])
        self.assertIsNone(model.call_args.args[2])
        self.assertEqual(snapshot.values["technical_plan"]["confirmation_status"], "pending_user_confirmation")

    async def test_failed_preparation_restores_original_stop_credential(self):
        """新执行登记失败时，回退本次交接，保留原停止凭据和原 checkpoint。"""
        await self.stopped(ApplicationLifecycleStage.ANALYZING_REQUIREMENT, "requirements")
        before = load_application_lifecycle(self.h.workspace)
        with patch("app.services.execution_recovery.observe_execution_started", AsyncMock(side_effect=OSError("controlled write failure"))):
            events = await self.send("补充失败也要保留原入口")
        self.assertTrue(any(event["type"] == "RUN_ERROR" for event in events))
        after = load_application_lifecycle(self.h.workspace)
        self.assertEqual(after.initialization, before.initialization)
        self.assertEqual(after.ended_planning_execution, before.ended_planning_execution)
        self.assertIsNone(after.active_run_id)
        snapshot = await self.h.graph.aget_state({"configurable": {"thread_id": self.h.thread_id}})
        self.assertEqual(snapshot.values["active_run_id"], self.h.source_run_id)

    def test_feedback_boundary_rejects_empty_unknown_and_non_restart_payloads(self):
        """空文本、客户端节点注入及借普通重试携带意见均在协议边界拒绝。"""
        for action, feedback in [("retry_current_failure", {"request": "修改"}), ("restart_initial_planning", {"request": " "}),
                ("restart_initial_planning", {"request": "修改", "node": "build"})]:
            with self.subTest(action=action, feedback=feedback), self.assertRaises(ValueError):
                _parse_request({"forwardedProps": {"workspaceRoot": str(self.h.workspace),
                    "executionRecovery": {"action": action, "sourceRunId": "source", "feedback": feedback}}})


class FormalPlanningFeedbackTests(InitialPlanningFeedbackTests):
    """复用相同生产 Graph 验证正式修订三阶段、联合生成、重复提交与失败回退。"""

    async def asyncSetUp(self):
        """在原 fixture 中登记已批准修订，End 后必须恢复同一 changeId 和来源会话。"""
        await super().asyncSetUp()
        self.formal = True

    async def test_stopped_product_generation_returns_to_requirement_and_joint_confirmation(self):
        """产品级正式修订保持已批准起点，只调整 ProductPlan 后回到联合确认门。"""
        await self.stopped(ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT, "product_planning")
        with patch("app.graph.nodes.requirements.analyze_requirements_with_chat_model") as requirement, patch(
                "app.graph.nodes.product_planning.plan_product_with_chat_model",
                side_effect=lambda current, **_kwargs: create_product_plan(current)) as product:
            events = await self.send("补充页面产品验收说明")
        await self.assert_review(events, "requirement_document")
        requirement.assert_not_called()
        product.assert_called_once()

    async def test_workspace_change_keeps_revision_stopped(self):
        """正式修订的工作区变化后仍要求重新确认影响，不能借补充入口放宽原准入。"""
        await self.stopped(ApplicationLifecycleStage.ANALYZING_REQUIREMENT, "requirements")
        (self.h.workspace / "changed.txt").write_text("当前文件已变化")
        before = load_application_lifecycle(self.h.workspace)
        events = await self.send()
        self.assertTrue(any(event["type"] == "RUN_ERROR" for event in events))
        self.assertEqual(load_application_lifecycle(self.h.workspace), before)

    async def test_upstream_target_cannot_expand_approved_ui_revision(self):
        """UI 阶段停止后新意见不能借执行目标扩大为未经影响确认的需求变更。"""
        await self.stopped(ApplicationLifecycleStage.GENERATING_UI_DESIGNS, "ui_confirmation")
        before = load_application_lifecycle(self.h.workspace)
        events = await self.send(conversationTarget="requirements")
        self.assertTrue(any(event["type"] == "RUN_ERROR" for event in events))
        self.assertEqual(load_application_lifecycle(self.h.workspace), before)
