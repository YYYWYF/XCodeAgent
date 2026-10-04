"""从真实异常、Graph checkpoint 和 DurableExecution 进入 Build Native Recovery。"""

from __future__ import annotations

import tempfile
import unittest
import importlib
from pathlib import Path
from unittest.mock import patch

from langgraph.graph import END, START, StateGraph

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.execution_recovery import DurableExecutionStatus
from app.graph.state import ProjectState
from app.graph.subgraphs.build import run_build_scheduler
from app.persistence.checkpoints import workflow_checkpointer
from app.workspace.task_documents import load_build_execution_record
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
from app.services.execution_recovery_action_planner import (
    plan_business_node_reentry_action, plan_failed_node_reentry_action,
)
from app.services.execution_recovery_executor import prepare_native_recovery
from app.services.node_recovery_context import bind_node_recovery, bind_recovery_runtime
from app.services.workflow_reentry import BusinessTargetResolver, FailureTargetResolver, workflow_entry
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
            tasks = [
                {"id": name, "owner": "backend", "status": "pending",
                 "dependencies": [] if name == "A" else ["A"],
                 "change_scope": [{"operation": "add", "path": f"Backend/app/{name}.py"}]}
                for name in ("A", "B")
            ]
            state = _ready_build_state(raw_workspace, {
                "workspace": raw_workspace, "project_plan": {"version": "1.0.0"},
                "build_execution_scope": {"type": "application", "targetId": "application"},
                "build_task_plan": replace_build_task_plan_tasks({
                    "schema_version": "build-dag.v4",
                    "build_units": {"application:root": {
                        "id": "application:root", "kind": "application", "task_ids": ["A", "B"],
                    }},
                    "unit_graph": {"nodes": ["application:root"], "edges": []},
                }, tasks),
                "tasks": tasks, "active_run_id": source_run_id,
                "active_thread_id": thread_id,
            })
            dispatched: list[list[str]] = []

            def build_node(current: ProjectState) -> dict:
                """首次在调度器持久化 A 后抛错，child 领取原 Build Run。"""

                with bind_node_recovery(current, "build"):
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
            graph = builder.compile(checkpointer=await workflow_checkpointer(workspace=raw_workspace))
            await observe_execution_started(
                workspace=raw_workspace, project_id=None, thread_id=thread_id,
                run_id=source_run_id, workflow_scope="application", first_node="build",
            )
            await observe_node_started(
                workspace=raw_workspace, run_id=source_run_id, thread_id=thread_id,
                workflow_scope="application", node_name="build",
            )
            build_module = importlib.import_module("app.graph.subgraphs.build")
            original_apply = build_module._apply_scheduler_results

            def persist_then_escape(*args, **kwargs):
                """保留真实批次持久化，再模拟业务结果提交前的逃逸异常。"""

                original_apply(*args, **kwargs)
                raise RuntimeError("escaped after persisted A")

            with (
                self.assertRaisesRegex(RuntimeError, "escaped after persisted A") as raised,
                patch.object(build_module, "_apply_scheduler_results", side_effect=persist_then_escape),
                patch("app.graph.subgraphs.build.generate_data_sources_with_deep_agent", side_effect=runner),
            ):
                await graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
            source_record = load_build_execution_record(state, source_run_id)
            assert source_record is not None
            self.assertEqual(source_record["progress"]["tasks"][0]["status"], "completed")
            self.assertEqual(dispatched, [["A"]])
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
                    recovered = await graph.ainvoke(None, config=context.fork_config)
            finally:
                await stop_execution_heartbeat(context.heartbeat_task)
            self.assertEqual(dispatched, [["A"], ["B"]])
            self.assertEqual(recovered["build_run_id"], source_record["build_run_id"])

    async def test_sqlite_business_failure_recovers_own_build_checkpoint(self) -> None:
        """真实 SQLite loop checkpoint 无需 metadata.writes 即可恢复 A/B。"""

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            thread_id = "build-business-thread"
            source_run_id = "build-business-source"
            lifecycle = create_application_lifecycle(
                application_id="build-business-app", application_name="Build business recovery",
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
            tasks = [
                {"id": name, "owner": "backend", "status": "pending", "dependencies": [],
                 "change_scope": [{"operation": "add", "path": f"Backend/app/{name}.py"}]}
                for name in ("A", "B")
            ]
            state = _ready_build_state(raw_workspace, {
                "workspace": raw_workspace, "project_plan": {"version": "1.0.0"},
                "build_execution_scope": {"type": "application", "targetId": "application"},
                "build_task_plan": replace_build_task_plan_tasks({
                    "schema_version": "build-dag.v4",
                    "build_units": {"application:root": {
                        "id": "application:root", "kind": "application", "task_ids": ["A", "B"],
                    }},
                    "unit_graph": {"nodes": ["application:root"], "edges": []},
                }, tasks),
                "tasks": tasks, "active_run_id": source_run_id,
                "active_thread_id": thread_id,
            })
            calls: list[list[str]] = []

            def build_node(current: ProjectState) -> dict:
                """通过生产调度器生成业务失败及恢复结果。"""

                with bind_node_recovery(current, "build"):
                    return run_build_scheduler(current)

            def runner(**kwargs):
                """首次返回 B 的网络失败，其后记录并完成 B 重试。"""

                names = [task["id"] for task in kwargs["tasks"]]
                calls.append(names)
                results = []
                for task in kwargs["tasks"]:
                    if task["id"] == "B" and len(calls) == 1:
                        results.append({"task_id": "B", "owner": "backend", "status": "failed",
                                        "failure_category": "network_error"})
                    else:
                        _write_workspace_file(kwargs.get("workspace"), task["change_scope"][0]["path"])
                        results.append({"task_id": task["id"], "owner": "backend", "status": "completed"})
                return results

            builder = StateGraph(ProjectState)
            builder.add_node("workflow_entry", workflow_entry)
            builder.add_node("build", build_node)
            builder.add_edge(START, "workflow_entry")
            builder.add_edge("workflow_entry", "build")
            builder.add_edge("build", END)
            graph = builder.compile(checkpointer=await workflow_checkpointer(workspace=raw_workspace))
            await observe_execution_started(
                workspace=raw_workspace, project_id=None, thread_id=thread_id,
                run_id=source_run_id, workflow_scope="application", first_node="build",
            )
            await observe_node_started(
                workspace=raw_workspace, run_id=source_run_id, thread_id=thread_id,
                workflow_scope="application", node_name="build",
            )
            with (
                patch("app.graph.subgraphs.build.generate_data_sources_with_deep_agent", side_effect=runner),
                patch("app.graph.subgraphs.build._finalize_build", return_value=({}, {}, None, [], None)),
            ):
                failed = await graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(calls, [["A", "B"]])
            snapshots = [
                item async for item in graph.aget_state_history(
                    {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                )
            ]
            source_record = load_build_execution_record(state, source_run_id)
            assert source_record is not None
            self.assertEqual(source_record["build_run_id"], failed["build_run_id"])
            fail_workflow_lifecycle(raw_workspace, run_id=source_run_id, phase="build", error=RuntimeError("Build failed"))
            await observe_execution_failed(
                workspace=raw_workspace, run_id=source_run_id, thread_id=thread_id,
                workflow_scope="application", authoritative_node="build",
            )
            source = await get_execution(raw_workspace, source_run_id)
            assert source is not None
            reentry = await BusinessTargetResolver().resolve(
                workspace=raw_workspace, source=source, graph=graph,
            )
            result_snapshot = next(
                item for item in snapshots
                if item.values.get("status") == "failed"
                and item.parent_config
                and item.parent_config["configurable"]["checkpoint_id"]
                == reentry.context_authority.checkpoint_id
            )
            self.assertEqual(result_snapshot.metadata.get("source"), "loop")
            action = plan_business_node_reentry_action(
                workspace=raw_workspace, source=source, reentry_plan=reentry,
            )
            self.assertIsNotNone(action.primary_action)
            context = await prepare_native_recovery(
                workspace=raw_workspace, source_run_id=source_run_id,
                graph=graph, reentry_plan=reentry,
            )
            self.assertIsNotNone(context.internal_progress)
            self.assertEqual(context.internal_progress["build_run_id"], source_record["build_run_id"])
            self.assertEqual(
                result_snapshot.parent_config["configurable"]["checkpoint_id"],
                reentry.context_authority.checkpoint_id,
            )
            try:
                with (
                    bind_recovery_runtime(context.node_recovery_context()),
                    patch("app.graph.subgraphs.build.generate_data_sources_with_deep_agent", side_effect=runner),
                    patch("app.graph.subgraphs.build._finalize_build", return_value=({}, {}, None, [], None)),
                ):
                    recovered = await graph.ainvoke(None, config=context.fork_config)
            finally:
                await stop_execution_heartbeat(context.heartbeat_task)
            self.assertEqual(calls, [["A", "B"], ["B"]])
            self.assertEqual(recovered["build_summary"]["status"], "completed")
