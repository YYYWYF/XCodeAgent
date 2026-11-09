"""跨阶段线程交接后的真实 AG-UI、产物失效、确认及恢复链路。"""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app.agents.design_conversation import DesignConversationDecision
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.protocols.application_page_planning import build_application_page_planning_ag_ui_stream
from app.protocols.application_planning_interrupt import application_planning_interrupt_from_snapshot
from app.protocols.planning_stage_handoff import PlanningStageHandoffRequest, handoff_planning_stage
from app.services.application_lifecycle import load_application_lifecycle
from app.tools.ask_user import clear_clarification
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class PlanningStageThreadJourneyTests(unittest.IsolatedAsyncioTestCase):
    """模型调用使用受控替身，其余 Graph、存储、AG-UI 和确认门均使用真实实现。"""

    async def asyncSetUp(self):
        """生成待确认技术计划，准备回到独立需求线程。"""

        self.h = TechnicalPlanningRecoveryJourneyHarness()
        self.settings = patch("app.config.Settings.from_env", side_effect=self.h.current_settings)
        self.settings.start()
        await self.h.initialize_graph()
        with patch("app.graph.nodes.planning._generate_valid_technical_plan", return_value=(deepcopy(self.h.candidate), [], None)):
            await self.h.run_initial()
        self.original_thread = self.h.thread_id
        self.config = {"configurable": {"thread_id": self.original_thread}}

    async def asyncTearDown(self):
        """关闭本测试工作区连接，不影响真实服务。"""

        await close_workflow_checkpointer_for_workspace(workspace=str(self.h.workspace))
        self.h.close()
        self.settings.stop()

    async def _handoff(self, phase, target):
        """以最新版本交接一次独立阶段线程。"""

        lifecycle = load_application_lifecycle(self.h.workspace)
        snapshot = await self.h.graph.aget_state(self.config)
        result = await handoff_planning_stage(self.h.graph, PlanningStageHandoffRequest(
            workspaceRoot=str(self.h.workspace), applicationId=self.h.project_id,
            sourceThreadId=self.config["configurable"]["thread_id"], sourceRunId=snapshot.values["active_run_id"],
            lifecycleRevision=lifecycle.revision, targetPhase=phase), target)
        self.config = {"configurable": {"thread_id": target}}
        return result

    async def _submit(self, action, request=""):
        """通过目标线程的真实 Native Interrupt 提交当前动作，收集标准 AG-UI 事件。"""

        snapshot = await self.h.graph.aget_state(self.config)
        pending = application_planning_interrupt_from_snapshot(snapshot)
        payload = {"threadId": self.config["configurable"]["thread_id"], "request": request or "继续规划",
            "forwardedProps": {"workspaceRoot": str(self.h.workspace), "applicationPlanningInteraction": {
                **{key: pending[key] for key in ("gateId", "artifact", "artifactRevision")}, "action": action, "request": request}}}
        frames = [frame async for frame in build_application_page_planning_ag_ui_stream(graph=self.h.graph, payload=payload)]
        events = [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]
        self.assertEqual(events[-1]["type"], "RUN_FINISHED", str(events[-1]))
        self.assertFalse(any(event["type"] == "RUN_ERROR" for event in events), str(events[-1]))
        return await self.h.graph.aget_state(self.config)

    async def test_historical_requirement_question_is_rejected_without_model_or_writes(self):
        """进入计划后历史需求入口不可提问，拒绝不改变技术确认门与生命周期。"""

        before = await self.h.graph.aget_state(self.config)
        lifecycle = load_application_lifecycle(self.h.workspace)
        with patch("app.agents.design_conversation.classify_design_conversation", return_value=
            DesignConversationDecision(intent="read_only", change_level="none", reason="只读提问", response="当前计划尚未确认。")) as model:
            frames = [frame async for frame in build_application_page_planning_ag_ui_stream(graph=self.h.graph,
                payload={"threadId": "independent-design-question", "forwardedProps": {"technicalPlanningConversation": {
                    "action": "classify", "workspaceRoot": str(self.h.workspace), "applicationId": self.h.project_id,
                    "planningThreadId": self.original_thread, "conversationPhase": "product", "request": "现在技术规划确认了吗？"}}})]
        events = [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]
        result = next(event["value"] for event in events if event.get("name") == "technical-planning-conversation")
        model.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertIn("仅供查看历史会话", result["error"]["message"])
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        old = await self.h.graph.aget_state({"configurable": {"thread_id": self.original_thread}})
        self.assertEqual(old.config, before.config)
        self.assertEqual(load_application_lifecycle(self.h.workspace), lifecycle)
        question = await self.h.graph.aget_state({"configurable": {"thread_id": "independent-design-question"}})
        self.assertFalse(question.values)

    async def test_requirement_revision_invalidates_plan_and_preserves_joint_confirmation(self):
        """技术到需求独立交接后修改上游，旧计划失效，不能跳过新的需求联合确认。"""

        await self._handoff("product", "revised-design-thread")
        with (
            patch("app.graph.application_planning_revision.classify_design_conversation", return_value=
                DesignConversationDecision(intent="requirement_change", change_level="requirement", reason="修改需求", affected_page_ids=[], response="")),
            patch("app.graph.nodes.requirements.analyze_requirements_with_chat_model", return_value={
                "requirement_spec": deepcopy(self.h.requirement_spec), "clarification": clear_clarification(self.h.requirement_spec)}),
            patch("app.graph.nodes.product_planning._generate_valid_product_plan", return_value=deepcopy(self.h.product_plan)),
            patch("app.graph.nodes.planning._generate_valid_technical_plan") as generate,
        ):
            result = await self._submit("design_change", "修改公告需求")
        generate.assert_not_called()
        self.assertFalse(result.values.get("technical_plan"))
        self.assertFalse(result.values.get("application_planning_confirmation"))
        self.assertEqual(application_planning_interrupt_from_snapshot(result)["artifact"], "requirement_document")
        with self.assertRaisesRegex(ValueError, "完成需求和 UI 确认"):
            await self._handoff("planning", "premature-plan-thread")

    async def test_new_thread_cold_recovery_keeps_gate_and_confirmation_can_complete(self):
        """需求入口交给技术新线程后生成、冷恢复、确认都不借用旧线程的恢复索引。"""

        from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
        from app.services.application_lifecycle import write_application_lifecycle
        current = load_application_lifecycle(self.h.workspace)
        write_application_lifecycle(self.h.workspace, current.model_copy(update={"initialization": current.initialization.model_copy(update={
            "stage": ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY, "status": ApplicationLifecycleStatus.AWAITING_USER})}))
        await self.h.graph.ainvoke({"resume_from": "planning_stage_entry"}, self.config)
        await self._handoff("planning", "new-plan-thread")
        def generate(*args, **kwargs):
            """验证实际生成调用继承目标 Graph 的模型会话身份。"""

            from langchain_core.runnables.config import ensure_config
            self.assertEqual(ensure_config()["configurable"]["thread_id"], "new-plan-thread")
            return deepcopy(self.h.candidate), [], None

        with patch("app.graph.nodes.planning._generate_valid_technical_plan", side_effect=generate):
            await self._submit("enter_planning")
        frames = [frame async for frame in build_application_page_planning_ag_ui_stream(graph=self.h.graph,
            payload={"threadId": "new-plan-thread", "forwardedProps": {"applicationPlanningRecovery": {
                "action": "get", "workspaceRoot": str(self.h.workspace), "applicationId": self.h.project_id}}})]
        events = [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]
        recovery = next(event["value"] for event in events if event.get("name") == "workflow-run")
        workflow = recovery["recoverySnapshot"]["workflow"]
        self.assertEqual(workflow["threadId"], "new-plan-thread")
        self.assertEqual(workflow["summary"]["status"], "requires_user_input")
        self.assertTrue((self.h.workspace / ".devagentstudio/drafts/plans/technical-plan.json").is_file())
        self.assertFalse((self.h.workspace / ".devagentstudio/plans/technical-plan.json").exists())
        result = await self._submit("confirm")
        self.assertEqual(result.values["technical_plan"]["confirmation_status"], "confirmed")
        self.assertFalse((self.h.workspace / ".devagentstudio/drafts/plans/technical-plan.json").exists())
        self.assertTrue((self.h.workspace / ".devagentstudio/plans/technical-plan.json").is_file())
        self.assertEqual(load_application_lifecycle(self.h.workspace).initialization.thread_id, "new-plan-thread")

    async def test_completed_product_history_cannot_start_conversation(self):
        """进入开发及后续阶段后历史需求入口拒绝对话，开发仍由正式修订入口提出修改。"""

        from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
        from app.services.application_lifecycle import write_application_lifecycle
        current = load_application_lifecycle(self.h.workspace)
        ready = current.model_copy(update={"initialization": current.initialization.model_copy(update={
            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH, "status": ApplicationLifecycleStatus.COMPLETED})})
        write_application_lifecycle(self.h.workspace, ready)
        before = await self.h.graph.aget_state(self.config)

        frames = [frame async for frame in build_application_page_planning_ag_ui_stream(graph=self.h.graph,
            payload={"threadId": "completed-product-thread", "forwardedProps": {"workspaceRoot": str(self.h.workspace),
                "workflowAction": "product_stage_conversation", "productStageConversation": {"request": "有哪些页面？"}}})]
        events = [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]
        result = next(event["value"] for event in events if event.get("name") == "workflow-run")
        self.assertEqual(result["status"], "failed")
        self.assertIn("开发阶段提出", result["error"]["message"])
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertEqual((await self.h.graph.aget_state(self.config)).config, before.config)
        self.assertFalse((await self.h.graph.aget_state({"configurable": {"thread_id": "completed-product-thread"}})).values)
        self.assertEqual(load_application_lifecycle(self.h.workspace), ready)

    async def _start_formal_revision_on_new_thread(self, branch):
        """准备已完成应用与原开发影响确认，再在新阶段线程真实启动正式修订。"""

        from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
        from app.domain.application_revision import RevisionImpact, RevisionTarget
        from app.services.application_lifecycle import write_application_lifecycle
        from app.services.application_revision_lifecycle import register_revision_impact
        from app.workspace.plan_documents import commit_technical_plan_document

        plan = deepcopy(self.h.candidate)
        plan["confirmation_status"] = "confirmed"
        commit_technical_plan_document({"workspace": str(self.h.workspace)}, plan)
        current = load_application_lifecycle(self.h.workspace)
        write_application_lifecycle(self.h.workspace, current.model_copy(update={"initialization": current.initialization.model_copy(update={
            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH, "status": ApplicationLifecycleStatus.COMPLETED})}))
        await self.h.graph.aupdate_state(self.config, {"technical_plan": plan, "status": "completed", "resume_from": ""}, as_node="template_reconcile")
        request = "修改页面说明" if branch == "design_stage_revision" else "修改技术架构说明"
        target = RevisionTarget(type="application")
        pending = register_revision_impact(self.h.workspace, interaction_id="impact-new-thread", source_thread_id="development-thread",
            source_run_id="development-run", request=request, target=target, impact=RevisionImpact(formalBranch=branch,
                revisionType="ui_visual_change" if branch == "design_stage_revision" else "technical_contract_change",
                earliestArtifact="requirement-spec" if branch == "design_stage_revision" else "technical-plan",
                affectedArtifacts=["requirement-spec", "product-plan", "ui-design", "technical-plan"] if branch == "design_stage_revision" else ["technical-plan"],
                affectedResources=[], reason="用户明确要求修改"))
        new_thread = f"formal-{branch}"
        await self._handoff("product" if branch == "design_stage_revision" else "planning", new_thread)
        current = load_application_lifecycle(self.h.workspace)
        self.assertEqual(current.pending_revision_impact.change_id, pending.change_id)
        self.assertEqual(current.pending_revision_impact.based_on_lifecycle_revision, current.revision)
        def product_for_current_requirement(requirement, **kwargs):
            """生成替身也绑定节点实际输入的需求摘要，避免用旧 fixture 伪造有效产品草稿。"""

            from app.services.product_plan import create_product_plan
            return create_product_plan(requirement, agent_plan=deepcopy(self.h.product_plan))

        with (
            patch("app.graph.nodes.requirements.analyze_requirements_with_chat_model", return_value={
                "requirement_spec": deepcopy(self.h.requirement_spec), "clarification": clear_clarification(self.h.requirement_spec)}),
            patch("app.graph.nodes.product_planning._generate_valid_product_plan", side_effect=product_for_current_requirement),
            patch("app.graph.nodes.planning._generate_valid_technical_plan", return_value=(deepcopy(self.h.candidate), [], None)),
        ):
            frames = [frame async for frame in build_application_page_planning_ag_ui_stream(graph=self.h.graph,
                payload={"threadId": new_thread, "request": request, "forwardedProps": {"workspaceRoot": str(self.h.workspace),
                    "workflowAction": "start_design_revision" if branch == "design_stage_revision" else "start_technical_revision",
                    "revisionRequest": {"source": "conversation_handoff", "formalBranch": branch, "request": request,
                        "target": target.model_dump(by_alias=True, exclude_none=True), "confirmedImpact": {"interactionId": pending.interaction_id}}}})]
        events = [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]
        self.assertFalse(any(event["type"] == "RUN_ERROR" for event in events), str(events[-1]))
        snapshot = await self.h.graph.aget_state(self.config)
        gate = application_planning_interrupt_from_snapshot(snapshot)
        self.assertEqual(gate["artifact"], "requirement_document" if branch == "design_stage_revision" else "technical_plan")
        revision = load_application_lifecycle(self.h.workspace).active_formal_revision
        self.assertEqual(revision.planning_thread_id, new_thread)
        self.assertEqual(revision.source_thread_id, "development-thread")

    async def test_design_formal_revision_starts_on_new_design_thread(self):
        """设计正式修订的原影响确认在交接后仍然有效，生成后必须联合确认。"""

        await self._start_formal_revision_on_new_thread("design_stage_revision")

    async def test_technical_formal_revision_starts_on_new_plan_thread(self):
        """技术专项修订使用新 PLAN thread，保留原开发来源并等待技术确认。"""

        await self._start_formal_revision_on_new_thread("workbench_plan_revision")

    async def test_formal_design_followup_can_reply_then_confirm_without_regeneration(self):
        """正式返回需求后的提问不重复执行首次 START，结构化确认仍可消费原审阅门。"""

        from pathlib import Path
        from app.services.stage_artifacts import _ARTIFACTS

        await self._start_formal_revision_on_new_thread("design_stage_revision")
        root = Path(self.h.workspace) / ".devagentstudio"
        paths = [root / prefix / relative for relative in _ARTIFACTS.values() for prefix in ("", "drafts")]
        before = {path: path.read_bytes() for path in paths if path.is_file()}
        with (
            patch("app.graph.application_planning_revision.classify_design_conversation", return_value=
                DesignConversationDecision(intent="read_only", change_level="none", reason="只读提问", response="正在等待需求文档联合确认。")) as classify,
            patch("app.graph.nodes.requirements.analyze_requirements_with_chat_model") as requirements,
            patch("app.graph.nodes.product_planning._generate_valid_product_plan") as product,
        ):
            replied = await self._submit("design_change", "当前是在等待联合确认吗？只回答，不修改。")
            self.assertEqual(replied.values["conversation_response"], "正在等待需求文档联合确认。")
            self.assertEqual(application_planning_interrupt_from_snapshot(replied)["artifact"], "requirement_document")
            await self._submit("design_change", "确认，继续")
        self.assertEqual(classify.call_count, 1)
        requirements.assert_not_called()
        product.assert_not_called()
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        confirmed = await self._submit("confirm")
        self.assertEqual(application_planning_interrupt_from_snapshot(confirmed)["artifact"], "ui_designs", str(confirmed.values.get("clarification")))
