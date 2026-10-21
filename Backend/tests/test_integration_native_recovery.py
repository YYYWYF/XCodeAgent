"""真实 checkpoint 和恢复事务覆盖测试阶段准备失败后的再次重试。"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from langgraph.graph import END, START, StateGraph

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.execution_recovery import DurableExecutionStatus, RecoveryAttemptStatus, RecoveryExecutionError, WorkflowReentryReason
from app.graph.workflow import build_graph
from app.graph.state import ProjectState
from app.persistence.checkpoints import workflow_checkpointer
from app.persistence.execution_recovery import get_execution, get_recovery_attempt, list_recovery_attempts_from_source, mark_execution_interrupted
from app.services.application_lifecycle import create_application_lifecycle, start_workbench_execution, write_application_lifecycle
from app.services.execution_recovery import observe_execution_started, observe_execution_finished
from app.services.execution_recovery_executor import WorkflowReentryExecutor, finalize_handed_off_recovery_attempt, prepare_native_recovery
from app.services.execution_lease_heartbeat import stop_execution_heartbeat
from app.services.workflow_reentry import BusinessTargetResolver, InterruptedTargetResolver
from app.services.node_recovery_context import bind_recovery_runtime
from app.services.execution_recovery_projection import resolve_execution_recovery_projection
from app.protocols.workflow.lifecycle import project_workflow_lifecycle_boundary


class IntegrationNativeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """验证工作区变化不阻断测试重入，重试不会重复 fork。"""

    async def test_business_failures_expose_retry_and_preserve_node_rules(self):
        """业务失败通过真实主图入口重试，保留进度额度且仍停在确认门。"""
        for node in ("integration_test", "small_task_repair"):
            with self.subTest(node=node), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                lifecycle = create_application_lifecycle(application_id="test", application_name="test")
                write_application_lifecycle(root, lifecycle.model_copy(update={
                    "initialization": lifecycle.initialization.model_copy(update={
                        "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                        "status": ApplicationLifecycleStatus.COMPLETED,
                    }),
                }))
                start_workbench_execution(root, scope="application", target_id="application", page_id=None,
                                         thread_id="thread", run_id="source", phase=node, owner_session_id="owner")
                await observe_execution_started(workspace=raw, project_id=None, thread_id="thread", run_id="source",
                                                workflow_scope=None, first_node=node, owner_session_id="owner")
                graph = build_graph(checkpointer=await workflow_checkpointer(workspace=raw))
                tasks = [{"id": "done", "status": "completed"}, {"id": "failed", "status": "failed"}]
                failed = {"phase": node, "status": "failed", "message": "集成检查或修复失败。",
                          "repair_iteration": 3, "small_task_tasks": tasks, "repair_tasks": tasks,
                          "integration_next_action": "handle_failure", "small_task_route": "handle_failure"}
                with patch(f"app.graph.workflow.nodes.{node}", return_value=failed):
                    await graph.ainvoke({
                        "workspace": raw, "active_run_id": "source", "active_thread_id": "thread",
                        "owner_session_id": "owner", "resume_from": node, "repair_iteration": 2,
                        "max_repair_iterations": 3, "unit_test_decision": "skip",
                        "quality_gate_passed": True,
                    }, {"configurable": {"thread_id": "thread"}})
                project_workflow_lifecycle_boundary(raw, run_id="source", node_name=node, update=failed)
                await observe_execution_finished(workspace=raw, run_id="source", thread_id="thread",
                                                 workflow_scope=None, status=DurableExecutionStatus.FAILED)
                source = await get_execution(raw, "source")
                self.assertIsNone(source.failure)
                plan = await BusinessTargetResolver().resolve(workspace=raw, source=source, graph=graph)
                self.assertEqual(plan.reason, WorkflowReentryReason.BUSINESS_RETRY)
                # 入口签发仍要求同一个 lifecycle 失败节点，不允许客户端借用其它目标。
                other = "small_task_repair" if node == "integration_test" else "integration_test"
                with self.assertRaises(RecoveryExecutionError):
                    await BusinessTargetResolver().resolve(workspace=raw, source=source.model_copy(update={"current_node": other}), graph=graph)
                projection = await resolve_execution_recovery_projection(raw, active_workbench_run_ids={"source"})
                candidate = projection.candidates[0]
                self.assertTrue(candidate.can_continue)
                self.assertEqual(candidate.recovery_action_plan.primary_action.target_node, node)
                context = await WorkflowReentryExecutor().prepare_business_retry(
                    workspace=raw, source_run_id="source", graph=graph, reentry_plan=plan,
                )
                try:
                    waiting = {"phase": node, "status": "requires_user_input",
                               "integration_next_action": "await_user_input", "small_task_route": "await_user_input",
                               "clarification": {"mode": "repair_scope_confirmation"}}
                    with bind_recovery_runtime(context.node_recovery_context()), patch(
                        "app.graph.nodes.workspace_inspection.inspect_workspace", return_value={"workspace_revision": "current"},
                    ), patch(f"app.graph.workflow.nodes.{node}", return_value=waiting) as resumed:
                        result = await graph.ainvoke(None, config=context.fork_config)
                    received = resumed.call_args.args[0]
                    self.assertEqual(received["repair_iteration"], 3)
                    self.assertEqual(received["max_repair_iterations"], 3)
                    self.assertEqual(received["unit_test_decision"], "skip")
                    self.assertFalse(received["quality_gate_passed"])
                    if node == "small_task_repair":
                        self.assertEqual([item["status"] for item in received["small_task_tasks"]], ["completed", "pending"])
                        self.assertEqual(received["repair_return_node"], "integration_test")
                    self.assertEqual(result["status"], "requires_user_input")
                finally:
                    await stop_execution_heartbeat(context.heartbeat_task)
                    if context.workspace_lease is not None:
                        context.workspace_lease.release()

    async def test_changed_workspace_recovery_preserves_budget_and_runs_once(self):
        """旧指纹不阻止恢复，保留修复预算且不重复准备已开始的事务。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            lifecycle = create_application_lifecycle(application_id="test", application_name="test")
            write_application_lifecycle(workspace, lifecycle.model_copy(update={
                "initialization": lifecycle.initialization.model_copy(update={
                    "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    "status": ApplicationLifecycleStatus.COMPLETED,
                }),
            }))
            start_workbench_execution(workspace, scope="application", target_id="application", page_id=None,
                                     thread_id="thread", run_id="source", phase="integration_test")

            def entry(state):
                """提交普通入口，留下可 fork 的确定后继。"""

                return {"status": "in_progress"}

            def integration(state):
                """本回归只验证恢复事务，不执行外部构建命令。"""

                return {"quality_gate_passed": True}

            builder = StateGraph(ProjectState)
            builder.add_node("entry", entry)
            builder.add_node("integration_test", integration)
            builder.add_edge(START, "entry")
            builder.add_edge("entry", "integration_test")
            builder.add_edge("integration_test", END)
            graph = builder.compile(checkpointer=await workflow_checkpointer(workspace=raw), interrupt_before=["integration_test"])
            await observe_execution_started(workspace=raw, project_id=None, thread_id="thread", run_id="source",
                                            workflow_scope="application", first_node="integration_test")
            await graph.ainvoke({"workspace": raw, "active_run_id": "source", "active_thread_id": "thread",
                                 "workspace_revision": "old", "repair_iteration": 2, "unit_test_decision": "skip"},
                                {"configurable": {"thread_id": "thread"}})
            await mark_execution_interrupted(workspace=workspace, run_id="source", interrupted_at=datetime.now(timezone.utc))
            source = await get_execution(raw, "source")
            resolution = await InterruptedTargetResolver().resolve(workspace=raw, source=source, graph=graph)
            self.assertEqual(resolution.kind, "continue")
            context = await prepare_native_recovery(
                workspace=raw, source_run_id="source", graph=graph, reentry_plan=resolution.reentry_plan,
            )
            attempt = (await list_recovery_attempts_from_source(raw, "source"))[0]
            try:
                self.assertEqual(context.recovery_plan.target_node, "integration_test")
                current = await graph.aget_state(context.fork_config)
                self.assertEqual(current.values["repair_iteration"], 2)
                self.assertEqual(current.values["unit_test_decision"], "skip")
                self.assertEqual(current.next, ("integration_test",))
                self.assertEqual((await get_recovery_attempt(raw, attempt.new_run_id)).status, RecoveryAttemptStatus.STARTED)
                # 已提交 fork 的事务再次请求不能进入相同准备流程。
                result = await finalize_handed_off_recovery_attempt(workspace=raw, new_run_id=attempt.new_run_id, graph=graph)
                self.assertEqual(result.status, RecoveryAttemptStatus.STARTED)
            finally:
                await stop_execution_heartbeat(context.heartbeat_task)
                if context.workspace_lease is not None:
                    context.workspace_lease.release()
