"""从真实异常、Graph checkpoint 和 DurableExecution 进入 Build Native Recovery。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.execution_recovery import DurableExecutionStatus
from app.graph.state import ProjectState
from app.graph.subgraphs.build import run_build_scheduler
from app.persistence.execution_recovery import get_execution
from app.protocols.workflow.lifecycle import fail_workflow_lifecycle
from app.services.application_lifecycle import (
    create_application_lifecycle, start_workbench_execution, write_application_lifecycle,
)
from app.services.build_task_planner import replace_build_task_plan_tasks
from app.services.execution_lease_heartbeat import stop_execution_heartbeat
from app.services.execution_recovery import (
    observe_execution_failed, observe_execution_started, observe_node_started,
)
from app.services.execution_recovery_action_planner import plan_failed_node_reentry_action
from app.services.execution_recovery_executor import prepare_native_recovery
from app.services.node_recovery_context import bind_node_recovery, bind_recovery_runtime
from app.services.workflow_reentry import FailureTargetResolver, workflow_entry
from tests.test_build_subgraph_scheduler import _ready_build_state, _write_workspace_file


class NativeBuildEntryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """验证未产生业务 failed checkpoint 的 Build 异常仍可从入口正常执行。"""

    async def test_escaped_build_error_reenters_without_business_failed_state(self) -> None:
        """串联真实 Graph、Durable failure、ActionPlan、fork 和 Build 调度。"""

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            thread_id = "build-entry-thread"
            source_run_id = "build-entry-source"
            lifecycle = create_application_lifecycle(
                application_id="build-entry-app", application_name="Build entry recovery",
            )
            lifecycle = lifecycle.model_copy(update={
                "initialization": lifecycle.initialization.model_copy(update={
                    "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    "status": ApplicationLifecycleStatus.COMPLETED,
                }),
            })
            write_application_lifecycle(workspace, lifecycle)
            start_workbench_execution(
                workspace, scope="application", target_id="application", page_id=None,
                thread_id=thread_id, run_id=source_run_id, phase="build",
            )
            task = {
                "id": "A", "owner": "backend", "status": "pending", "dependencies": [],
                "change_scope": [{"operation": "add", "path": "Backend/app/A.py"}],
            }
            state = _ready_build_state(raw_workspace, {
                "workspace": raw_workspace, "project_plan": {"version": "1.0.0"},
                "build_execution_scope": {"type": "application", "targetId": "application"},
                "build_task_plan": replace_build_task_plan_tasks({
                    "schema_version": "build-dag.v4",
                    "build_units": {"application:root": {
                        "id": "application:root", "kind": "application", "task_ids": ["A"],
                    }},
                    "unit_graph": {"nodes": ["application:root"], "edges": []},
                }, [task]),
                "tasks": [task], "active_run_id": source_run_id,
                "active_thread_id": thread_id,
            })
            fail_once = True
            dispatched: list[list[str]] = []

            def build_node(current: ProjectState) -> dict:
                """首次在业务返回前抛错，child 才运行真实 Build 调度器。"""

                nonlocal fail_once
                with bind_node_recovery(current, "build"):
                    if fail_once:
                        fail_once = False
                        raise RuntimeError("escaped before business result")
                    return run_build_scheduler(current)

            def runner(**kwargs):
                """替代外部 Agent，并记录真实派发的任务数。"""

                tasks = kwargs["tasks"]
                dispatched.append([item["id"] for item in tasks])
                for item in tasks:
                    _write_workspace_file(kwargs.get("workspace"), item["change_scope"][0]["path"])
                return [{"task_id": item["id"], "owner": "backend", "status": "completed"} for item in tasks]

            builder = StateGraph(ProjectState)
            builder.add_node("workflow_entry", workflow_entry)
            builder.add_node("build", build_node)
            builder.add_edge(START, "workflow_entry")
            builder.add_edge("workflow_entry", "build")
            builder.add_edge("build", END)
            graph = builder.compile(checkpointer=InMemorySaver())
            await observe_execution_started(
                workspace=raw_workspace, project_id=None, thread_id=thread_id,
                run_id=source_run_id, workflow_scope="application", first_node="build",
            )
            await observe_node_started(
                workspace=raw_workspace, run_id=source_run_id, thread_id=thread_id,
                workflow_scope="application", node_name="build",
            )
            with self.assertRaisesRegex(RuntimeError, "escaped before business result") as raised:
                await graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
            fail_workflow_lifecycle(raw_workspace, run_id=source_run_id, phase="build", error=raised.exception)
            await observe_execution_failed(
                workspace=raw_workspace, run_id=source_run_id, thread_id=thread_id,
                workflow_scope="application", exception=raised.exception,
                authoritative_node="build",
            )
            source = await get_execution(raw_workspace, source_run_id)
            assert source is not None
            self.assertEqual(source.status, DurableExecutionStatus.FAILED)
            entry = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            self.assertNotEqual(entry.values.get("status"), "failed")
            reentry = await FailureTargetResolver().resolve(
                workspace=raw_workspace, source=source, graph=graph,
            )
            action = plan_failed_node_reentry_action(
                workspace=raw_workspace, source=source, reentry_plan=reentry,
            )
            self.assertIsNotNone(action.primary_action)
            context = await prepare_native_recovery(
                workspace=raw_workspace, source_run_id=source_run_id,
                graph=graph, reentry_plan=reentry,
            )
            self.assertEqual(context.recovery_plan.checkpoint_id, reentry.context_authority.checkpoint_id)
            self.assertIsNone(context.internal_progress)
            try:
                with (
                    bind_recovery_runtime(context.node_recovery_context()),
                    patch("app.graph.subgraphs.build.generate_data_sources_with_deep_agent", side_effect=runner),
                    patch("app.graph.subgraphs.build._finalize_build", return_value=({}, {}, None, [], None)),
                ):
                    await graph.ainvoke(None, config=context.fork_config)
            finally:
                await stop_execution_heartbeat(context.heartbeat_task)
            self.assertEqual(dispatched, [["A"]])
