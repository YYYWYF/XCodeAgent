"""验证独立阶段线程的真实 Graph 确认门、单一归属和重复提交。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from app.graph.application_planning_workflow import build_application_planning_graph
from app.protocols.application_page_planning import build_application_page_planning_ag_ui_stream
from app.protocols.application_planning_interrupt import application_planning_interrupt_from_snapshot
from app.protocols.planning_stage_handoff import PlanningStageHandoffRequest, handoff_planning_stage, assert_current_planning_thread
from app.services.application_lifecycle import load_application_lifecycle
from tests.test_application_page_planning import _confirmed_state, _write_planning_stage_entry_lifecycle


class PlanningStageHandoffTests(unittest.IsolatedAsyncioTestCase):
    """新线程只承接最新正式上下文，旧线程不能继续推进或产生第二份有效分支。"""

    async def asyncSetUp(self):
        """准备真实的等待进入技术规划审阅门。"""

        self.directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.directory.name)
        self.source = "design-thread"
        self.target = "plan-thread"
        self.graph = build_application_planning_graph(checkpointer=InMemorySaver())
        _write_planning_stage_entry_lifecycle(self.workspace, initialization_thread_id=self.source)
        state = _confirmed_state(self.workspace)
        state.update(active_run_id="design-run", active_thread_id=self.source, resume_from="planning_stage_entry",
                     project_id=self.workspace.name, workflow_scope="application_planning", request="任务中心", timeline=["old-history"])
        await self.graph.ainvoke(state, {"configurable": {"thread_id": self.source}})
        self.lifecycle = load_application_lifecycle(self.workspace)
        self.request = PlanningStageHandoffRequest(workspaceRoot=str(self.workspace), applicationId=self.workspace.name,
            sourceThreadId=self.source, sourceRunId="design-run", lifecycleRevision=self.lifecycle.revision, targetPhase="planning")

    async def asyncTearDown(self):
        """清理本测试创建的临时工作区。"""

        self.directory.cleanup()

    async def test_handoff_preserves_gate_and_confirmed_artifacts_without_generating(self):
        """设计到技术使用独立 Graph thread，未消费入口确认，也不调用模型。"""

        source = await self.graph.aget_state({"configurable": {"thread_id": self.source}})
        with patch("app.graph.nodes.planning._generate_valid_technical_plan") as generate:
            workflow = await handoff_planning_stage(self.graph, self.request, self.target)
        generate.assert_not_called()
        target = await self.graph.aget_state({"configurable": {"thread_id": self.target}})
        self.assertEqual(workflow["threadId"], self.target)
        self.assertEqual(target.values["requirement_spec"], source.values["requirement_spec"])
        self.assertEqual(target.values["product_plan"], source.values["product_plan"])
        self.assertNotIn("old-history", target.values.get("timeline", []))
        self.assertNotEqual(target.values["active_run_id"], "design-run")
        old_gate = application_planning_interrupt_from_snapshot(source)
        new_gate = application_planning_interrupt_from_snapshot(target)
        self.assertEqual(new_gate["gateId"], old_gate["gateId"])
        self.assertEqual(load_application_lifecycle(self.workspace).initialization.thread_id, self.target)
        self.assertEqual(self.lifecycle.initialization.iteration_id, self.source)
        self.assertEqual(load_application_lifecycle(self.workspace).initialization.iteration_id, self.source)
        self.assertEqual(source.config, (await self.graph.aget_state(source.config)).config)
        with self.assertRaisesRegex(ValueError, "已交接"):
            assert_current_planning_thread(str(self.workspace), self.source)

    async def test_replay_returns_same_target_and_other_target_is_rejected(self):
        """断线重试幂等复用同一目标，不能从旧线程交接出并行有效分支。"""

        first = await handoff_planning_stage(self.graph, self.request, self.target)
        again = await handoff_planning_stage(self.graph, self.request, self.target)
        self.assertEqual(first["runId"], again["runId"])
        with self.assertRaisesRegex(ValueError, "规划状态已变化"):
            await handoff_planning_stage(self.graph, self.request, "another-plan-thread")

    async def test_invalid_source_or_same_thread_cannot_change_lifecycle(self):
        """旧版本、其他来源及相同线程均在写入当前归属前拒绝。"""

        with self.assertRaisesRegex(ValueError, "不同"):
            await handoff_planning_stage(self.graph, self.request, self.source)
        with self.assertRaisesRegex(ValueError, "来源已过期"):
            await handoff_planning_stage(self.graph, self.request.model_copy(update={"sourceRunId": "old-run"}), self.target)
        self.assertEqual(load_application_lifecycle(self.workspace), self.lifecycle)

    async def test_protocol_reports_complete_ag_ui_lifecycle(self):
        """交接成功和业务拒绝均产生标准消息、结果、快照和运行终态。"""

        for target, status in ((self.target, "completed"), ("another-thread", "failed")):
            frames = [frame async for frame in build_application_page_planning_ag_ui_stream(graph=self.graph,
                payload={"threadId": target, "forwardedProps": {"planningStageHandoff": self.request.model_dump()}})]
            events = [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]
            self.assertEqual(events[0]["type"], "RUN_STARTED")
            self.assertEqual(events[-1]["type"], "RUN_FINISHED")
            self.assertTrue(any(event["type"] == "STATE_SNAPSHOT" for event in events))
            result = next(event["value"] for event in events if event.get("name") == "planning-stage-handoff")
            self.assertEqual(result["status"], status)

    async def test_finished_context_can_enter_new_formal_revision_thread(self):
        """已完成规划交给新修订线程时只保存上下文，不重跑生成或复制旧消息。"""

        from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
        from app.services.application_lifecycle import write_application_lifecycle
        current = load_application_lifecycle(self.workspace)
        ready = current.model_copy(update={"initialization": current.initialization.model_copy(update={
            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH, "status": ApplicationLifecycleStatus.COMPLETED})})
        write_application_lifecycle(self.workspace, ready)
        await self.graph.aupdate_state({"configurable": {"thread_id": self.source}},
            {"status": "completed", "resume_from": ""}, as_node="template_reconcile")
        workflow = await handoff_planning_stage(self.graph, self.request.model_copy(update={"targetPhase": "product"}), self.target)
        self.assertEqual(workflow["threadId"], self.target)
        target = await self.graph.aget_state({"configurable": {"thread_id": self.target}})
        self.assertFalse(target.next)
        self.assertIsNone(application_planning_interrupt_from_snapshot(target))

    async def test_formal_revision_changes_thread_without_changing_source_or_change(self):
        """设计修订交给技术线程只更新执行归属，保留原开发来源、changeId 与目标。"""

        from app.domain.application_revision import ActiveFormalRevision
        from app.services.application_lifecycle import write_application_lifecycle
        current = load_application_lifecycle(self.workspace)
        active = ActiveFormalRevision(changeId="change-A", formalBranch="design_stage_revision",
            sourceThreadId="development-source", sourceRunId="development-run", request="修改需求",
            target={"type": "application"}, impactInteractionId="impact-A", planningThreadId=self.source,
            status="design_planning", currentArtifact="ui-design", remainingArtifacts=["technical-plan"])
        write_application_lifecycle(self.workspace, current.model_copy(update={"active_formal_revision": active}))
        await handoff_planning_stage(self.graph, self.request, self.target)
        updated = load_application_lifecycle(self.workspace).active_formal_revision
        self.assertEqual(updated.planning_thread_id, self.target)
        self.assertEqual(updated.change_id, active.change_id)
        self.assertEqual(updated.source_thread_id, active.source_thread_id)
        self.assertEqual(updated.target, active.target)
