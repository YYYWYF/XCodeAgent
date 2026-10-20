"""失败中的技术规划自由输入必须保留恢复资格，并复用原生成节点。"""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app.agents.technical_planning_conversation import (
    TechnicalPlanningConversationDecision, classify_technical_planning_conversation,
)
from app.domain.application_revision import ActiveFormalRevision, RevisionTarget
from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.services.application_revision_lifecycle import ensure_technical_plan_generation_lifecycle
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.persistence.execution_recovery import get_execution
from app.protocols.application_page_planning import build_application_page_planning_ag_ui_stream
from app.services.application_lifecycle import load_application_lifecycle, persist_application_lifecycle_transition
from app.services.product_plan import create_product_plan
from app.tools.ask_user import clear_clarification
from app.services.technical_planning_retry import prepare_technical_planning_retry
from app.services.execution_recovery_executor import (
    _technical_request_from_values, _validate_formal_revision_transaction_identity,
)
from app.domain.application_planning_recovery import application_planning_sha256
from app.workspace.plan_documents import commit_technical_plan_document
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class TechnicalPlanningConversationTests(unittest.IsolatedAsyncioTestCase):
    """覆盖只读提问、已有计划修改、无计划拒绝及版本漂移。"""

    async def asyncSetUp(self):
        """准备真实 Graph、已确认上游与一次生成失败。"""
        self.h = TechnicalPlanningRecoveryJourneyHarness()
        self.settings = patch("app.config.Settings.from_env", side_effect=self.h.current_settings)
        self.settings.start()
        await self.h.initialize_graph()
        with patch("app.graph.nodes.planning._generate_valid_technical_plan", side_effect=RuntimeError("模型请求失败")):
            await self.h.run_initial()
        self.config = {"configurable": {"thread_id": self.h.thread_id}}

    async def asyncTearDown(self):
        """关闭工作区连接和模型配置替身。"""
        await close_workflow_checkpointer_for_workspace(workspace=str(self.h.workspace))
        self.h.close()
        self.settings.stop()

    async def _send(self, action="classify", request="为什么报错？", **identity):
        """经原 AG-UI 端点发送对话动作，提取结构化结果与完整生命周期。"""
        frames = [f async for f in build_application_page_planning_ag_ui_stream(
            graph=self.h.graph,
            payload={"threadId": "independent-question-thread", "runId": f"message-{action}",
                     "forwardedProps": {"technicalPlanningConversation": {
                         "action": action, "workspaceRoot": str(self.h.workspace),
                         "applicationId": self.h.project_id, "planningThreadId": self.h.thread_id,
                         "request": request, **identity,
                     }}},
        )]
        events = [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        results = [e["value"] for e in events if e.get("name") == "technical-planning-conversation"]
        return results[-1] if results else {}, events

    def _seed_plan(self):
        """写入已有计划，问答和修改都必须以此为基线。"""
        plan = deepcopy(self.h.candidate)
        plan["summary"] = "已有技术规划"
        commit_technical_plan_document({"workspace": str(self.h.workspace)}, plan)
        return plan

    async def _seed_template_failure(self):
        """复现截图：TechnicalPlan 已确认，原规划线程停在模板生成失败。"""
        plan = self._seed_plan()
        plan["confirmation_status"] = "confirmed"
        commit_technical_plan_document({"workspace": str(self.h.workspace)}, plan)
        for stage, status in (
            (ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION, ApplicationLifecycleStatus.AWAITING_USER),
            (ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES, ApplicationLifecycleStatus.RUNNING),
            (ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED, ApplicationLifecycleStatus.FAILED),
        ):
            persist_application_lifecycle_transition(self.h.workspace, stage=stage, status=status,
                                                     active_run_id=self.h.source_run_id)
        await self.h.graph.aupdate_state(self.config, {
            "technical_plan": plan, "phase": "template_reconcile", "status": "failed",
            "resume_from": "", "application_planning_interaction": {},
        }, as_node="template_reconcile")

    async def test_template_failure_accepts_requirement_revision_and_waits_for_confirmation(self):
        """模板失败后按截图增加页面，真实 Graph 回到需求并停在联合文档确认门。"""
        await self._seed_template_failure()
        request = "@需求设计 我还加一个页面，叫随机生成名字"
        decision, _ = await self._send(request=request)
        self.assertEqual(decision.get("intent"), "design_change")
        spec = deepcopy(self.h.requirement_spec)
        spec["pages"].append({"pageId": "random_name", "name": "随机生成名字", "path": "/random-name",
                              "module_id": spec["feature_modules"][0]["id"], "description": "随机生成名字"})
        product = create_product_plan(spec)
        with (
            patch("app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                  return_value={"requirement_spec": spec, "clarification": clear_clarification(spec)}) as analyze,
            patch("app.graph.nodes.product_planning._generate_valid_product_plan", return_value=product),
            patch("app.graph.nodes.planning.plan_project_with_chat_model") as technical,
        ):
            _, events = await self._send("revise_design", request, sourceRunId=decision["sourceRunId"],
                                         checkpointId=decision["checkpointId"])
        self.assertFalse(any(event["type"] == "RUN_ERROR" for event in events))
        analyze.assert_called_once()
        technical.assert_not_called()
        snapshot = await self.h.graph.aget_state(self.config)
        self.assertEqual(snapshot.values["design_change_target"], "requirements")
        self.assertIn("random_name", [page["pageId"] for page in snapshot.values["requirement_spec"]["pages"]])
        self.assertTrue(any(task.interrupts for task in snapshot.tasks))
        self.assertEqual(load_application_lifecycle(self.h.workspace).initialization.stage,
                         ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION)
        # 计划页仍显示同一会话输入框时，上游审阅门也能继续接收对话，不再误报阶段错误。
        followup, _ = await self._send(request="@需求设计 调整新页面名称")
        self.assertEqual(followup.get("intent"), "design_change")
        self.assertEqual(snapshot.config, (await self.h.graph.aget_state(self.config)).config)

    async def test_template_failure_accepts_technical_revision(self):
        """模板失败后的 @技术规划仍复用已确认计划，生成后重新等待确认。"""
        await self._seed_template_failure()
        request = "@技术规划 修改前端架构说明"
        decision, _ = await self._send(request=request)
        self.assertEqual(decision.get("intent"), "revise")
        with (
            patch("app.graph.nodes.planning.plan_project_with_chat_model", return_value=deepcopy(self.h.candidate)) as generate,
            patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
        ):
            _, events = await self._send("revise", request, sourceRunId=decision["sourceRunId"],
                                         checkpointId=decision["checkpointId"])
        self.assertFalse(any(event["type"] == "RUN_ERROR" for event in events))
        generate.assert_called_once()
        snapshot = await self.h.graph.aget_state(self.config)
        self.assertEqual(tuple(snapshot.next), ("technical_planning_review",))

    async def test_question_does_not_consume_failed_execution(self):
        """报错时提问不改变 checkpoint、修改要求、lifecycle 或上方恢复动作。"""
        self._seed_plan()
        before = await self.h.graph.aget_state(self.config)
        lifecycle = load_application_lifecycle(self.h.workspace)
        _, _, recovery_before = await self.h.resolve_action()
        with patch("app.protocols.technical_planning_conversation.classify_technical_planning_conversation",
                   return_value=TechnicalPlanningConversationDecision(intent="reply", message="模型请求失败，原修改要求仍保留。")):
            reply, _ = await self._send()
        self.assertEqual(reply["intent"], "reply")
        after = await self.h.graph.aget_state(self.config)
        self.assertEqual(before.config, after.config)
        self.assertEqual(before.values, after.values)
        self.assertEqual(lifecycle, load_application_lifecycle(self.h.workspace))
        _, _, recovery_after = await self.h.resolve_action()
        self.assertEqual(recovery_before["sourceRunId"], recovery_after["sourceRunId"])
        self.assertEqual((await get_execution(self.h.workspace, self.h.source_run_id)).status.value, "failed")

    async def test_revision_uses_original_generator_with_new_request_and_existing_plan(self):
        """失败后输入新修改要求，携带已有计划调用原生成器并停回正式确认门。"""
        self._seed_plan()
        request = "在前端架构说明追加技术问答修改标记。"
        with patch("app.protocols.technical_planning_conversation.classify_technical_planning_conversation",
                   return_value=TechnicalPlanningConversationDecision(intent="revise", message="修改技术规划。")):
            decision, _ = await self._send(request=request)
        with (
            patch("app.graph.nodes.planning.plan_project_with_chat_model", return_value=deepcopy(self.h.candidate)) as generate,
            patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
        ):
            _, events = await self._send("revise", request, sourceRunId=decision["sourceRunId"], checkpointId=decision["checkpointId"])
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[0]["planning_adjustment_request"], request)
        self.assertEqual(generate.call_args.kwargs["existing_plan"]["summary"], "已有技术规划")
        self.assertFalse(any(e["type"] == "RUN_ERROR" for e in events))
        final = await self.h.graph.aget_state(self.config)
        self.assertEqual(final.values["request"], request)
        self.assertEqual(final.values["design_change_generation_request"], request)
        self.assertEqual(tuple(final.next), ("technical_planning_review",))
        self.assertTrue(any(task.interrupts for task in final.tasks))

    async def test_no_plan_directs_to_existing_retry_and_refuses_generation(self):
        """无计划即便模型判为修改，也只引导上方重试，直接修改请求同样被拒绝。"""
        before = await self.h.graph.aget_state(self.config)
        with patch("app.protocols.technical_planning_conversation.classify_technical_planning_conversation",
                   return_value=TechnicalPlanningConversationDecision(intent="revise", message="修改计划。")):
            decision, _ = await self._send(request="改一下架构")
        self.assertEqual(decision["intent"], "reply")
        self.assertIn("上方重试", decision["message"])
        rejected, _ = await self._send("revise", "改一下架构", sourceRunId=decision["sourceRunId"], checkpointId=decision["checkpointId"])
        self.assertEqual(rejected["status"], "failed")
        self.assertIn("上方重试", rejected["error"]["message"])
        self.assertEqual(before.config, (await self.h.graph.aget_state(self.config)).config)

    async def test_stale_decision_cannot_override_new_state(self):
        """分类后出现新状态时，拒绝旧输入，不覆盖其他执行的计划或要求。"""
        self._seed_plan()
        snapshot = await self.h.graph.aget_state(self.config)
        await self.h.graph.aupdate_state(self.config, {"request": "另一轮最新输入", "resume_from": "technical_planning"}, as_node="workflow_entry")
        rejected, _ = await self._send("revise", "旧输入", sourceRunId=self.h.source_run_id,
                                       checkpointId=snapshot.config["configurable"]["checkpoint_id"])
        self.assertEqual(rejected["status"], "failed")
        self.assertIn("状态已变化", rejected["error"]["message"])
        self.assertEqual((await self.h.graph.aget_state(self.config)).values["request"], "另一轮最新输入")

    async def test_natural_confirmation_is_read_only(self):
        """自由文本确认不能交给分类模型授权生成或推进正式确认。"""
        self._seed_plan()
        before = await self.h.graph.aget_state(self.config)
        with patch("app.agents.technical_planning_conversation.create_chat_model") as model:
            reply, _ = await self._send(request="确认")
        model.assert_not_called()
        self.assertEqual(reply["intent"], "reply")
        self.assertIn("确认卡", reply["message"])
        self.assertEqual(before.config, (await self.h.graph.aget_state(self.config)).config)

    async def test_ui_mention_returns_to_design_graph_without_a_technical_plan(self):
        """无技术计划的失败现场也能按 @UI设计回到上游，重新停在设计确认门。"""
        with patch("app.agents.technical_planning_conversation.create_chat_model") as model:
            decision, _ = await self._send(request="@UI设计 重新调整页面布局")
        model.assert_not_called()
        self.assertEqual(decision["intent"], "design_change")
        _, events = await self._send(
            "revise_design", "@UI设计 重新调整页面布局",
            sourceRunId=decision["sourceRunId"], checkpointId=decision["checkpointId"],
        )
        snapshot = await self.h.graph.aget_state(self.config)
        self.assertEqual(snapshot.values.get("design_change_target"), "ui_confirmation")
        self.assertTrue(snapshot.tasks)
        self.assertTrue(any(task.interrupts for task in snapshot.tasks))
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")

    def test_plain_requirement_change_can_be_classified_as_upstream_change(self):
        """未写 @ 的需求修改也保留意图识别能力，不被限定为只读回答。"""
        with patch("app.agents.technical_planning_conversation.create_chat_model") as model:
            model.return_value.invoke.return_value.content = json.dumps({
                "intent": "design_change", "message": "返回需求设计增加审核角色。",
            })
            decision = classify_technical_planning_conversation("增加审核角色", {})
        self.assertEqual(decision.intent, "design_change")
        self.assertIn("design_change", model.return_value.invoke.call_args.args[0])

    async def test_design_change_keeps_checkpoint_version_guard(self):
        """上游回退和技术修改一样拒绝分类后已过期的 checkpoint。"""
        snapshot = await self.h.graph.aget_state(self.config)
        await self.h.graph.aupdate_state(self.config, {"request": "更新后的输入", "resume_from": "technical_planning"}, as_node="workflow_entry")
        rejected, _ = await self._send(
            "revise_design", "@需求设计 增加角色", sourceRunId=self.h.source_run_id,
            checkpointId=snapshot.config["configurable"]["checkpoint_id"],
        )
        self.assertEqual(rejected["status"], "failed")
        self.assertIn("状态已变化", rejected["error"]["message"])

    async def test_design_revision_feedback_retains_formal_revision_identity(self):
        """设计修订到达技术确认后仍可修改，不把原需求修订改成新正式修订。"""
        lifecycle = load_application_lifecycle(self.h.workspace)
        active = ActiveFormalRevision(
            changeId="existing-design-change", formalBranch="design_stage_revision",
            sourceThreadId="conversation", sourceRunId="source", request="原需求修改",
            target=RevisionTarget(type="application"), impactInteractionId="impact",
            planningThreadId=self.h.thread_id, status="design_planning",
            currentArtifact="ui-design", remainingArtifacts=["technical-plan"],
        )
        for stage in (
            ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
            ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
        ):
            with self.subTest(stage=stage):
                current = lifecycle.model_copy(update={
                    "active_formal_revision": active,
                    "initialization": lifecycle.initialization.model_copy(update={
                        "stage": stage, "status": ApplicationLifecycleStatus.AWAITING_USER,
                    }),
                })
                with (
                    patch("app.services.application_revision_lifecycle._required_lifecycle", return_value=current),
                    patch("app.services.application_revision_lifecycle.restart_application_planning_lifecycle", return_value=current) as restart,
                ):
                    result = ensure_technical_plan_generation_lifecycle(
                        self.h.workspace, active_run_id="feedback", thread_id=self.h.thread_id,
                        change_id=active.change_id,
                    )
                self.assertEqual(result.active_formal_revision, active)
                restart.assert_called_once_with(self.h.workspace, stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN, active_run_id="feedback")
                _validate_formal_revision_transaction_identity(values={
                    "change_id": active.change_id,
                    "change_target": active.target.model_dump(mode="json"),
                }, lifecycle=current)

    async def test_retry_retains_input_authority_after_clearing_interaction(self):
        """重试清空交互后，原业务要求仍是生成节点与事务边界共同的权威输入。"""
        snapshot = await self.h.graph.aget_state(self.config)
        state = {
            **snapshot.values,
            "application_planning_interaction": {"action": "revise", "request": "本轮已确认的技术修改要求"},
            "design_change_generation_request": "上一轮残留的要求",
        }
        updated = {**state, **prepare_technical_planning_retry(state)}
        self.assertEqual(_technical_request_from_values(updated), "本轮已确认的技术修改要求")
        self.assertEqual(updated["application_planning_recovery_boundary"]["requestSha256"],
                         application_planning_sha256("本轮已确认的技术修改要求"))
