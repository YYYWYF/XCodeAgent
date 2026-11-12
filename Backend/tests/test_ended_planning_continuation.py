"""正式修订停止后沿既有节点恢复，保持工作区校验和新产物确认。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.types import interrupt

from app.graph.state import ProjectState
from app.persistence.execution_recovery import get_execution
from app.protocols.application_page_planning import _build_application_planning_recovery_projection
from app.protocols.execution_recovery import _resolve_action_source_run, build_execution_recovery_ag_ui_stream
from app.protocols.workflow.run_control import WorkflowRunRegistry
from app.services.application_lifecycle import application_lifecycle_payload, load_application_lifecycle
from app.services.application_lifecycle import claim_application_planning_run_for_recovery, write_application_lifecycle
from app.services.business_node_retry import prepare_business_node_retry
from app.services.ended_planning import ended_planning_available
from app.services.planning_execution_end import end_planning_execution
from app.services.retry_preparation import abandon_prepared_retry
from tests import test_planning_execution_end as end_fixture


class EndedPlanningContinuationTests(unittest.IsolatedAsyncioTestCase):
    """使用真实取消、checkpoint、lifecycle 和内容指纹验证停止与继续。"""

    async def fixture(self, workspace, *, skills=None, initial_creation=False):
        """建立首次生成或正式需求修订，在实际生成节点中取消并保留原请求现场。"""
        await end_fixture.PlanningExecutionEndTests.prepare(self, workspace)
        if initial_creation:
            from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus

            initial = load_application_lifecycle(workspace)
            write_application_lifecycle(workspace, initial.model_copy(update={
                "active_formal_revision": None,
                "initialization": initial.initialization.model_copy(update={
                    "stage": ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
                    "status": ApplicationLifecycleStatus.RUNNING,
                }),
            }))
        started = asyncio.Event()
        calls, downstream = [], []

        async def requirements(state):
            """首轮等待取消，继续时检查完整请求并生成新的待确认文档。"""
            calls.append(dict(state))
            if len(calls) == 1:
                started.set()
                await asyncio.Event().wait()
            return {"status": "requires_user_input", "phase": "requirements_review"}

        def requirements_review(state):
            """新版本仍要求明确确认，继续生成不能代替用户批准。"""
            interrupt({"message": "请联合确认需求与产品计划"})
            return {}

        def next_stage(state):
            """记录下游是否越过明确确认边界。"""
            downstream.append(True)
            return {"status": "completed"}

        builder = StateGraph(ProjectState)
        builder.add_node("requirements", requirements)
        builder.add_node("requirements_review", requirements_review)
        builder.add_node("next_stage", next_stage)
        builder.add_edge(START, "requirements")
        builder.add_edge("requirements", "requirements_review")
        builder.add_edge("requirements_review", "next_stage")
        builder.add_edge("next_stage", END)
        graph = builder.compile(checkpointer=InMemorySaver())
        registry = WorkflowRunRegistry()
        self.enterContext(patch("app.protocols.workflow.run_control.workflow_run_registry", registry))
        task = asyncio.create_task(graph.ainvoke({
            "active_run_id": "design-run", "active_thread_id": "thread", "workspace": workspace,
            "workflow_scope": "application_planning", "request": "增加年度报表 ORIGINAL",
            "selected_skill_names": ["retained-skill"] if skills is None else skills,
            "application_planning_interaction": {"action": "confirm"},
        }, {"configurable": {"thread_id": "thread"}}))
        registry.register("design-run", task, workspace=workspace)
        await asyncio.wait_for(started.wait(), 5)
        before = load_application_lifecycle(workspace)
        ended = await end_planning_execution(workspace, "thread")
        return graph, before, ended, calls, downstream

    async def test_stop_continue_reexecutes_original_node_and_waits_for_confirmation(self):
        """动作确实重进原节点、恢复原修订并消费凭据，不能静默跳过新确认。"""
        with tempfile.TemporaryDirectory() as workspace:
            graph, before, ended, calls, downstream = await self.fixture(workspace)
            self.assertIsNone(ended.active_formal_revision)
            self.assertIsNone(ended.active_run_id)
            self.assertTrue(application_lifecycle_payload(ended)["endedPlanningExecution"]["workspaceUnchanged"])
            source = await get_execution(workspace, "design-run")
            snapshot = await graph.aget_state({"configurable": {"thread_id": "thread"}})
            projection = await _build_application_planning_recovery_projection(
                workspace=workspace, thread_id="thread", graph=graph, snapshot=snapshot, source=source)
            self.assertTrue(projection.can_continue)
            plan = projection.recovery_action_plan
            self.assertEqual(plan["primaryAction"]["label"], "继续生成")
            with patch("app.protocols.execution_recovery.application_planning_graph_for_request", AsyncMock(return_value=graph)):
                self.assertEqual(await _resolve_action_source_run(workspace=workspace,
                    incident_id=plan["incidentId"], action_id=plan["primaryAction"]["actionId"]), "design-run")
            context = await prepare_business_node_retry(source, graph)
            current = load_application_lifecycle(workspace)
            self.assertEqual(current.active_formal_revision.change_id, before.active_formal_revision.change_id)
            self.assertEqual(current.active_run_id, context.new_run_id)
            self.assertIsNone(current.ended_planning_execution)
            self.assertEqual(context.recovery_plan.target_node, "requirements")
            result = await graph.ainvoke(context.fork_snapshot.values, context.fork_config)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[-1]["request"], "增加年度报表 ORIGINAL")
            self.assertEqual(calls[-1]["selected_skill_names"], ["retained-skill"])
            self.assertEqual(calls[-1]["application_planning_interaction"], {})
            self.assertIn("__interrupt__", result)
            self.assertFalse(downstream)
            with self.assertRaises(Exception):
                await prepare_business_node_retry(source, graph)

    async def test_initial_generation_uses_same_stop_continue_authority(self):
        """复现首次生成停止无继续入口：使用同一 End 凭据、ActionPlan 和业务节点重试。"""
        with tempfile.TemporaryDirectory() as workspace:
            graph, _, ended, calls, downstream = await self.fixture(workspace, initial_creation=True)
            self.assertEqual(ended.initialization.status, "cancelled")
            self.assertNotEqual(ended.initialization.stage, "ready_for_workbench")
            self.assertIsNone(ended.ended_planning_execution.formal_revision)
            source = await get_execution(workspace, "design-run")
            snapshot = await graph.aget_state({"configurable": {"thread_id": "thread"}})
            projection = await _build_application_planning_recovery_projection(
                workspace=workspace, thread_id="thread", graph=graph, snapshot=snapshot, source=source)
            self.assertTrue(projection.can_continue)
            self.assertEqual(projection.recovery_action_plan["primaryAction"]["label"], "继续生成")
            context = await prepare_business_node_retry(source, graph)
            current = load_application_lifecycle(workspace)
            self.assertIsNone(current.active_formal_revision)
            self.assertIsNone(current.ended_planning_execution)
            self.assertEqual(current.active_run_id, context.new_run_id)
            result = await graph.ainvoke(context.fork_snapshot.values, context.fork_config)
            self.assertEqual(len(calls), 2)
            self.assertIn("__interrupt__", result)
            self.assertFalse(downstream)

    async def test_file_change_denies_continue_and_keeps_released_state(self):
        """真实内容变化阻止继续，不能重新占用原修订或调用生成器。"""
        with tempfile.TemporaryDirectory() as workspace:
            graph, _, ended, calls, _ = await self.fixture(workspace)
            Path(workspace, "business.txt").write_text("changed")
            self.assertFalse(ended_planning_available(workspace, ended))
            source = await get_execution(workspace, "design-run")
            with self.assertRaises(Exception):
                await prepare_business_node_retry(source, graph)
            self.assertEqual(len(calls), 1)
            self.assertIsNone(load_application_lifecycle(workspace).active_formal_revision)

    async def test_changed_initial_creation_restarts_explicitly_and_waits_for_confirmation(self):
        """首次创建文件变化后沿 AG-UI 显式重生成，不能跳到开发或复用旧继续授权。"""
        with tempfile.TemporaryDirectory() as workspace:
            graph, _, ended, calls, downstream = await self.fixture(workspace, initial_creation=True, skills=[])
            Path(workspace, "business.txt").write_text("updated input")
            projected = application_lifecycle_payload(ended)["endedPlanningExecution"]
            self.assertFalse(projected["workspaceUnchanged"])
            self.assertTrue(projected["canRestart"])
            source = await get_execution(workspace, "design-run")
            with self.assertRaises(Exception):
                await prepare_business_node_retry(source, graph)
            with patch("app.protocols.execution_recovery.recovery_graph_for_execution", AsyncMock(return_value=graph)):
                frames = [frame async for frame in build_execution_recovery_ag_ui_stream(payload={
                    "threadId": "thread", "runId": "restart-request",
                    "forwardedProps": {"workspaceRoot": workspace, "executionRecovery": {
                        "action": "restart_initial_planning", "sourceRunId": source.run_id,
                    }},
                })]
            self.assertFalse(any('"RUN_ERROR"' in frame for frame in frames), "\n".join(frames))
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[-1]["request"], "增加年度报表 ORIGINAL")
            self.assertFalse(downstream)
            snapshot = await graph.aget_state({"configurable": {"thread_id": "thread"}})
            self.assertTrue(any(task.interrupts for task in snapshot.tasks))
            current = load_application_lifecycle(workspace)
            self.assertNotEqual(current.initialization.stage, "ready_for_workbench")
            self.assertIsNone(current.ended_planning_execution)
            with self.assertRaises(Exception):
                await prepare_business_node_retry(source, graph, restart_initial_planning=True)

    async def test_initial_restart_rejects_formal_revision_and_new_task_during_claim(self):
        """新入口不扩大正式修订权限，最终 CAS 仍复核新任务占用。"""
        for initial in (False, True):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as workspace:
                graph, _, ended, _, _ = await self.fixture(workspace, initial_creation=initial)
                source = await get_execution(workspace, "design-run")
                if not initial:
                    self.assertFalse(application_lifecycle_payload(ended)["endedPlanningExecution"]["canRestart"])
                    with self.assertRaises(Exception):
                        await prepare_business_node_retry(source, graph, restart_initial_planning=True)
                else:
                    # 模拟读资格后另一个任务取得所有权，最终 claim 必须拒绝消费原凭据。
                    latest = load_application_lifecycle(workspace)
                    write_application_lifecycle(workspace, latest.model_copy(update={"active_run_id": "other-run"}))
                    with self.assertRaises(Exception):
                        claim_application_planning_run_for_recovery(workspace, new_run_id="restart-run",
                            thread_id="thread", source_run_id=source.run_id, restart_initial_planning=True,
                            expected_lifecycle_revision=latest.revision)
                    self.assertIsNotNone(load_application_lifecycle(workspace).ended_planning_execution)

    async def test_preparation_failure_restores_ended_credential(self):
        """交接尚未执行业务时失败可回退原凭据，保留再次点击继续的资格。"""
        with tempfile.TemporaryDirectory() as workspace:
            graph, _, _, _, _ = await self.fixture(workspace)
            source = await get_execution(workspace, "design-run")
            context = await prepare_business_node_retry(source, graph)
            await abandon_prepared_retry(workspace=workspace, source=source,
                before=context.retry_lifecycle_before_handoff, new_run_id=context.new_run_id)
            current = load_application_lifecycle(workspace)
            self.assertIsNone(current.active_formal_revision)
            self.assertIsNone(current.active_run_id)
            self.assertTrue(ended_planning_available(workspace, current))

    async def test_final_claim_rechecks_files_and_new_task_occupancy(self):
        """旧按钮检查成功不授权未来提交，CAS 入场仍按当前文件和任务占用核验。"""
        with tempfile.TemporaryDirectory() as workspace:
            _, _, ended, _, _ = await self.fixture(workspace)
            self.assertTrue(ended_planning_available(workspace, ended))
            Path(workspace, "late-change.txt").write_text("changed after check")
            with self.assertRaisesRegex(Exception, "工作区或当前任务已变化"):
                claim_application_planning_run_for_recovery(workspace, new_run_id="child", thread_id="thread",
                    source_run_id="design-run", expected_lifecycle_revision=ended.revision)
            current = load_application_lifecycle(workspace)
            self.assertIsNotNone(current.ended_planning_execution)
            write_application_lifecycle(workspace, current.model_copy(update={"active_run_id": "new-run"}))
            self.assertFalse(ended_planning_available(workspace, load_application_lifecycle(workspace)))

    async def test_ag_ui_execute_runs_generation_and_returns_new_confirmation(self):
        """公共恢复端点必须实际调用原业务节点并输出完整 AG-UI 生命周期。"""
        with tempfile.TemporaryDirectory() as workspace:
            graph, _, _, calls, downstream = await self.fixture(workspace, skills=[])
            source = await get_execution(workspace, "design-run")
            snapshot = await graph.aget_state({"configurable": {"thread_id": "thread"}})
            projection = await _build_application_planning_recovery_projection(
                workspace=workspace, thread_id="thread", graph=graph, snapshot=snapshot, source=source)
            plan = projection.recovery_action_plan
            frames = []
            with patch("app.protocols.execution_recovery.application_planning_graph_for_request", AsyncMock(return_value=graph)), \
                 patch("app.protocols.execution_recovery.recovery_graph_for_execution", AsyncMock(return_value=graph)):
                async for frame in build_execution_recovery_ag_ui_stream(payload={
                    "threadId": "thread", "runId": "continue-request", "forwardedProps": {
                        "workspaceRoot": workspace, "executionRecovery": {"action": "execute",
                            "incidentId": plan["incidentId"], "actionId": plan["primaryAction"]["actionId"]}
                    }
                }, accept="text/event-stream"):
                    frames.extend(json.loads(line[5:]) for line in frame.splitlines() if line.startswith("data:"))
            self.assertEqual(frames[0]["type"], "RUN_STARTED")
            self.assertEqual(frames[-1]["type"], "RUN_FINISHED")
            self.assertIn("STATE_SNAPSHOT", [frame["type"] for frame in frames])
            self.assertEqual(len(calls), 2, frames)
            self.assertFalse(downstream)
