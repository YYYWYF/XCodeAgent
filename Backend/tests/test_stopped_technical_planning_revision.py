"""验证主动停止后，新的明确修改沿原 AG-UI 流程生成并重新等待确认。"""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from app.agents.technical_planning_conversation import classify_technical_planning_conversation

from app.domain.execution_recovery import DurableExecutionStatus
from app.persistence.checkpoints import close_workflow_checkpointer_for_workspace
from app.persistence.execution_recovery import get_execution
from app.protocols.application_page_planning import build_application_page_planning_ag_ui_stream
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.domain.application_lifecycle import ApplicationLifecycleStage
from app.services.application_lifecycle import load_application_lifecycle, write_application_lifecycle
from app.services.planning_execution_end import end_planning_execution
from app.workspace.plan_documents import commit_technical_plan_document, technical_plan_json_path
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class StoppedTechnicalPlanningRevisionTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 SQLite、Graph 和生命周期，仅替换生成模型。"""

    async def asyncSetUp(self):
        """建立已有技术规划，生成进入审阅后经生产 End 结束任务。"""
        self.h = TechnicalPlanningRecoveryJourneyHarness()
        self.settings = patch("app.config.Settings.from_env", side_effect=self.h.current_settings)
        self.settings.start()
        await self.h.initialize_graph()
        self.config = {"configurable": {"thread_id": self.h.thread_id}}
        plan = deepcopy(self.h.candidate)
        plan["summary"] = "停止前已有技术规划"
        commit_technical_plan_document({"workspace": str(self.h.workspace)}, plan)
        with patch("app.graph.nodes.planning._generate_valid_technical_plan", return_value=(plan, [], None)):
            await self.h.run_initial()
        await end_planning_execution(str(self.h.workspace), self.h.thread_id)

    async def asyncTearDown(self):
        """关闭真实持久化连接并释放隔离工作区。"""
        await close_workflow_checkpointer_for_workspace(workspace=str(self.h.workspace))
        self.h.close()
        self.settings.stop()

    async def _send(self, action="classify", request="@技术规划 调整架构说明", **identity):
        """通过生产 AG-UI 端点分类或提交修改，并保留完整帧供失败定位。"""
        frames = [frame async for frame in build_application_page_planning_ag_ui_stream(
            graph=self.h.graph, payload={
                "threadId": self.h.thread_id, "runId": f"after-stop-{action}",
                "forwardedProps": {"technicalPlanningConversation": {
                    "action": action, "workspaceRoot": str(self.h.workspace),
                    "applicationId": self.h.project_id, "planningThreadId": self.h.thread_id,
                    "request": request, **identity,
                }},
            },
        )]
        events = [json.loads(line[5:]) for frame in frames for line in frame.splitlines()
                  if line.startswith("data:")]
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED", events[-1])
        replies = [event["value"] for event in events if event.get("name") == "technical-planning-conversation"]
        return replies[-1] if replies else {}, events

    async def _verify_new_modification(self, *, from_card=False):
        """停止保留原记录，新修改生成真实文件并停回原生确认门，绝不自动确认。"""
        before = load_application_lifecycle(self.h.workspace)
        self.assertEqual(before.initialization.status, "cancelled")
        request = "调整架构说明" if from_card else "@技术规划 调整架构说明"
        with patch("app.protocols.technical_planning_conversation.classify_technical_planning_conversation",
                   wraps=classify_technical_planning_conversation) as classify:
            decision, _ = await self._send("prepare_revision" if from_card else "classify", request)
        if from_card:
            classify.assert_not_called()
        self.assertIn("intent", decision, decision)
        self.assertEqual(decision["intent"], "revise")
        self.assertEqual(before, load_application_lifecycle(self.h.workspace))
        candidate = deepcopy(self.h.candidate)
        candidate["summary"] = "停止后按新意见修改的技术规划"
        with (
            patch("app.graph.nodes.planning.plan_project_with_chat_model", return_value=candidate) as generate,
            patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
        ):
            _, events = await self._send("revise", request, sourceRunId=decision["sourceRunId"], checkpointId=decision["checkpointId"])
        errors = [event for event in events if event["type"] == "RUN_ERROR"]
        self.assertEqual(errors, [], errors)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[0]["planning_adjustment_request"], request)
        self.assertEqual(generate.call_args.kwargs["existing_plan"]["summary"], "停止前已有技术规划")
        final = await self.h.graph.aget_state(self.config)
        self.assertEqual(tuple(final.next), ("technical_planning_review",))
        self.assertTrue(any(task.interrupts for task in final.tasks))
        self.assertEqual(final.values["technical_plan"]["confirmation_status"], "pending_user_confirmation")
        disk = json.loads(technical_plan_json_path({"workspace": str(self.h.workspace)}).read_text())
        self.assertEqual(disk["summary"], candidate["summary"])
        self.assertEqual(disk["confirmation_status"], "pending_user_confirmation")
        current = load_application_lifecycle(self.h.workspace)
        self.assertEqual(current.initialization.status, "awaiting_user")
        self.assertEqual(current.initialization.stage, "awaiting_technical_plan_confirmation")
        self.assertEqual(current.initialization.thread_id, before.initialization.thread_id)
        self.assertEqual(current.initialization.iteration_id, before.initialization.iteration_id)
        self.assertIsNone(current.active_formal_revision)
        old = await get_execution(self.h.workspace, self.h.source_run_id)
        self.assertEqual(old.status, DurableExecutionStatus.CANCELLED)
        self.assertIsNotNone(old.ended_at)
        new = await get_execution(self.h.workspace, current.active_run_id)
        self.assertEqual(new.status, DurableExecutionStatus.AWAITING_USER)
        self.assertNotEqual(new.run_id, old.run_id)

    async def test_modification_after_stopping_review(self):
        """等待确认时结束任务，新要求可以生成但仍须再次确认。"""
        await self._verify_new_modification()

    async def test_card_modification_skips_classifier_and_reuses_generator(self):
        """卡片输入无需 @，零意图分类调用，仍以原计划进入同一生成器和确认门。"""
        await self._verify_new_modification(from_card=True)

    async def test_modification_after_stopping_generation(self):
        """生成中停止保留相同 CANCELLED 合同，沿新要求进入生成与审阅。"""
        current = load_application_lifecycle(self.h.workspace)
        write_application_lifecycle(self.h.workspace, current.model_copy(update={
            "initialization": current.initialization.model_copy(update={
                "stage": ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
            }),
        }), expected_revision=current.revision)
        await self._verify_new_modification()

    async def test_plain_retry_does_not_reopen_stopped_execution(self):
        """没有新的明确修改要求时，普通阶段重试仍拒绝复活用户已结束的执行。"""
        before = load_application_lifecycle(self.h.workspace)
        with patch("app.graph.nodes.planning.plan_project_with_chat_model") as generate:
            frames = [frame async for frame in build_workflow_ag_ui_stream(graph=self.h.graph, payload={
                "threadId": self.h.thread_id, "runId": "plain-retry-after-stop", "request": "继续执行",
                "resume_from": "technical_planning",
                "forwardedProps": {"workspaceRoot": str(self.h.workspace), "workflowScope": "application_planning"},
            })]
        generate.assert_not_called()
        self.assertIn("predecessor", "".join(frames))
        self.assertEqual(before, load_application_lifecycle(self.h.workspace))

    async def test_stale_modification_does_not_reopen_stopped_lifecycle(self):
        """分类后的版本失效保持停止状态，不能借新输入覆盖最新规划。"""
        decision, _ = await self._send()
        before = load_application_lifecycle(self.h.workspace)
        await self.h.graph.aupdate_state(self.config, {"request": "新版本", "resume_from": "technical_planning"}, as_node="workflow_entry")
        rejected, _ = await self._send("revise", sourceRunId=decision["sourceRunId"], checkpointId=decision["checkpointId"])
        self.assertEqual(rejected["status"], "failed")
        self.assertIn("状态已变化", rejected["error"]["message"])
        self.assertEqual(before, load_application_lifecycle(self.h.workspace))

    async def test_natural_confirmation_does_not_reopen_stopped_lifecycle(self):
        """自然语言确认只能答复，不重启或确认停止前的候选。"""
        before = load_application_lifecycle(self.h.workspace)
        reply, _ = await self._send(request="确认")
        self.assertEqual(reply["intent"], "reply")
        self.assertEqual(before, load_application_lifecycle(self.h.workspace))
