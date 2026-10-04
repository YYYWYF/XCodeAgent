"""从真实异常、Graph checkpoint 和 DurableExecution 进入 Build Native Recovery。"""

from __future__ import annotations

import tempfile
import unittest
import importlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from unittest.mock import patch

from langgraph.graph import END, START, StateGraph

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.execution_recovery import DurableExecutionStatus, WorkflowReentryReason
from app.graph.state import ProjectState
from app.graph.subgraphs.build import run_build_scheduler
from app.persistence.checkpoints import workflow_checkpointer
from app.workspace.task_documents import load_build_execution_record
from app.persistence.execution_recovery import get_execution, mark_execution_interrupted
from app.persistence.execution_recovery import list_recovery_attempts_from_source
from app.protocols.workflow.lifecycle import fail_workflow_lifecycle
from app.protocols.execution_recovery import build_execution_recovery_ag_ui_stream
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
from app.services.workflow_reentry import BusinessTargetResolver, FailureTargetResolver, InterruptedTargetResolver, workflow_entry
from tests.test_build_subgraph_scheduler import _ready_build_state, _write_workspace_file


class NativeBuildEntryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """验证未产生业务 failed checkpoint 的 Build 异常仍可从入口正常执行。"""

    async def test_escaped_build_error_reenters_without_business_failed_state(self) -> None:
        """串联真实 Graph、Durable failure、ActionPlan、fork 和 Build 调度。"""

        await self._exercise_escaped_build_entry(interrupted=False)

    async def test_interrupted_build_continues_completed_progress(self) -> None:
        """后端中断仍通过 InterruptedTargetResolver 保留已完成任务。"""

        await self._exercise_escaped_build_entry(interrupted=True)

    async def test_ordinary_interrupt_does_not_retry_unrelated_failure(self) -> None:
        """普通中断继续执行 pending C，但不会把独立失败的 B 变为已授权重试。"""

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            thread_id = "build-ordinary-interrupt-thread"
            source_run_id = "build-ordinary-interrupt-source"
            lifecycle = create_application_lifecycle(
                application_id="build-ordinary-interrupt-app", application_name="Build ordinary interrupt",
            )
            write_application_lifecycle(workspace, lifecycle.model_copy(update={
                "initialization": lifecycle.initialization.model_copy(update={
                    "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    "status": ApplicationLifecycleStatus.COMPLETED,
                }),
            }))
            start_workbench_execution(
                workspace, scope="application", target_id="application", page_id=None,
                thread_id=thread_id, run_id=source_run_id, phase="build",
            )
            tasks = [
                {"id": name, "owner": "backend", "status": "pending",
                 "dependencies": ["A"] if name == "C" else [],
                 "change_scope": [{"operation": "add", "path": f"Backend/app/{name}.py"}]}
                for name in ("A", "B", "C")
            ]
            state = _ready_build_state(raw_workspace, {
                "workspace": raw_workspace, "project_plan": {"version": "1.0.0"},
                "build_execution_scope": {"type": "application", "targetId": "application"},
                "build_task_plan": replace_build_task_plan_tasks({
                    "schema_version": "build-dag.v4",
                    "build_units": {"application:root": {
                        "id": "application:root", "kind": "application", "task_ids": ["A", "B", "C"],
                    }},
                    "unit_graph": {"nodes": ["application:root"], "edges": []},
                }, tasks),
                "tasks": tasks, "active_run_id": source_run_id, "active_thread_id": thread_id,
            })
            calls: Counter[str] = Counter()
            calls_lock = Lock()

            def build_node(current: ProjectState) -> dict:
                """保持真实节点领取流程。"""

                with bind_node_recovery(current, "build"):
                    return run_build_scheduler(current)

            def runner(**kwargs):
                """首次批次让 B 失败，其他任务写入授权文件。"""

                results = []
                for task in kwargs["tasks"]:
                    with calls_lock:
                        calls[task["id"]] += 1
                    if task["id"] == "B":
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
            build_module = importlib.import_module("app.graph.subgraphs.build")
            original_apply = build_module._apply_scheduler_results

            def persist_then_stop(*args, **kwargs):
                """在批次进度落盘后模拟进程中断，保留真实 checkpoint 入口。"""

                original_apply(*args, **kwargs)
                raise RuntimeError("backend stopped")

            with (
                self.assertRaisesRegex(RuntimeError, "backend stopped"),
                patch.object(build_module, "_apply_scheduler_results", side_effect=persist_then_stop),
                patch("app.graph.subgraphs.build.generate_data_sources_with_deep_agent", side_effect=runner),
            ):
                await graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
            source_record = load_build_execution_record(state, source_run_id)
            assert source_record is not None
            self.assertEqual(
                {task["id"]: task["status"] for task in source_record["progress"]["tasks"]},
                {"A": "completed", "B": "failed", "C": "pending"},
            )
            await mark_execution_interrupted(
                workspace=workspace, run_id=source_run_id,
                interrupted_at=datetime.now(timezone.utc),
            )
            source = await get_execution(raw_workspace, source_run_id)
            assert source is not None
            resolution = await InterruptedTargetResolver().resolve(
                workspace=raw_workspace, source=source, graph=graph,
            )
            self.assertEqual(resolution.kind, "continue")
            assert resolution.reentry_plan is not None
            self.assertEqual(resolution.reentry_plan.reason, WorkflowReentryReason.INTERRUPTED_CONTINUE)
            with (
                patch("app.protocols.execution_recovery.workflow_graph_for_request", return_value=graph),
                patch("app.graph.subgraphs.build.generate_data_sources_with_deep_agent", side_effect=runner),
            ):
                frames = [frame async for frame in build_execution_recovery_ag_ui_stream(
                    payload={"forwardedProps": {
                        "workspaceRoot": raw_workspace,
                        "executionRecovery": {"action": "continue", "sourceRunId": source_run_id},
                    }},
                )]
            self.assertFalse(any('"type":"RUN_ERROR"' in frame for frame in frames), "".join(frames))
            self.assertEqual(calls, Counter({"A": 1, "B": 1, "C": 1}))

    async def _exercise_escaped_build_entry(self, *, interrupted: bool) -> None:
        """在首次批次持久化后模拟退出，再从真实节点入口重建执行。"""

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
            if interrupted:
                await mark_execution_interrupted(
                    workspace=workspace, run_id=source_run_id,
                    interrupted_at=datetime.now(timezone.utc),
                )
            else:
                fail_workflow_lifecycle(raw_workspace, run_id=source_run_id, phase="build", error=raised.exception)
                await observe_execution_failed(
                    workspace=raw_workspace, run_id=source_run_id, thread_id=thread_id,
                    workflow_scope="application", exception=raised.exception,
                    authoritative_node="build",
                )
            source = await get_execution(raw_workspace, source_run_id)
            assert source is not None
            self.assertEqual(
                source.status,
                DurableExecutionStatus.INTERRUPTED if interrupted else DurableExecutionStatus.FAILED,
            )
            entry = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            self.assertNotEqual(entry.values.get("status"), "failed")
            if interrupted:
                resolution = await InterruptedTargetResolver().resolve(
                    workspace=raw_workspace, source=source, graph=graph,
                )
                self.assertEqual(resolution.kind, "continue")
                reentry = resolution.reentry_plan
                assert reentry is not None
                self.assertEqual(reentry.reason, WorkflowReentryReason.INTERRUPTED_CONTINUE)
            else:
                reentry = await FailureTargetResolver().resolve(
                    workspace=raw_workspace, source=source, graph=graph,
                )
                action = plan_failed_node_reentry_action(
                    workspace=raw_workspace, source=source, reentry_plan=reentry,
                )
                self.assertIsNotNone(action.primary_action)
            if interrupted:
                with (
                    patch("app.protocols.execution_recovery.workflow_graph_for_request", return_value=graph),
                    patch("app.graph.subgraphs.build.generate_data_sources_with_deep_agent", side_effect=runner),
                    patch("app.graph.subgraphs.build._finalize_build", return_value=({}, {}, None, [], None)),
                ):
                    frames = [frame async for frame in build_execution_recovery_ag_ui_stream(
                        payload={"forwardedProps": {
                            "workspaceRoot": raw_workspace,
                            "executionRecovery": {"action": "continue", "sourceRunId": source_run_id},
                        }},
                    )]
                self.assertFalse(any('"type":"RUN_ERROR"' in frame for frame in frames), "".join(frames))
                attempts = await list_recovery_attempts_from_source(raw_workspace, source_run_id)
                self.assertEqual(len(attempts), 1)
                child_record = load_build_execution_record(state, attempts[0].new_run_id)
                assert child_record is not None
                recovered_build_run_id = child_record["build_run_id"]
            else:
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
                recovered_build_run_id = recovered["build_run_id"]
            self.assertEqual(dispatched, [["A"], ["B"]])
            self.assertEqual(recovered_build_run_id, source_record["build_run_id"])
            if interrupted and os.environ.get("BUILD_RECOVERY_EVIDENCE"):
                print(json.dumps({
                    "scenario": "interrupted_continue", "source": source_run_id,
                    "child": attempts[0].new_run_id, "reason": reentry.reason.value,
                    "entry_checkpoint": reentry.context_authority.checkpoint_id,
                    "build_run": source_record["build_run_id"],
                    "progress_source": child_record["progress_source_run_id"],
                    "calls": dispatched,
                }, ensure_ascii=False))

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
            calls: Counter[str] = Counter()
            calls_lock = Lock()

            def build_node(current: ProjectState) -> dict:
                """通过生产调度器生成业务失败及恢复结果。"""

                with bind_node_recovery(current, "build"):
                    return run_build_scheduler(current)

            def runner(**kwargs):
                """首次返回 B 的网络失败，其后记录并完成 B 重试。"""

                results = []
                for task in kwargs["tasks"]:
                    with calls_lock:
                        calls[task["id"]] += 1
                        attempt = calls[task["id"]]
                    if task["id"] == "B" and attempt == 1:
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
            self.assertEqual(calls, Counter({"A": 1, "B": 1}))
            snapshots = [
                item async for item in graph.aget_state_history(
                    {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                )
            ]
            source_record = load_build_execution_record(state, source_run_id)
            assert source_record is not None
            self.assertEqual(source_record["build_run_id"], failed["build_run_id"])
            self.assertEqual(
                {task["id"]: task["status"] for task in source_record["progress"]["tasks"]},
                {"A": "completed", "B": "failed"},
            )
            self.assertEqual(
                next(result for result in source_record["progress"]["build_results"]
                     if result["task_id"] == "B")["failure_category"],
                "network_error",
            )
            self.assertTrue(snapshots)
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
            self.assertEqual(
                result_snapshot.parent_config["configurable"]["checkpoint_id"],
                reentry.context_authority.checkpoint_id,
            )
            with (
                patch("app.protocols.execution_recovery.workflow_graph_for_request", return_value=graph),
                patch("app.graph.subgraphs.build.generate_data_sources_with_deep_agent", side_effect=runner),
                patch("app.graph.subgraphs.build._finalize_build", return_value=({}, {}, None, [], None)),
            ):
                frames = [frame async for frame in build_execution_recovery_ag_ui_stream(
                    payload={"forwardedProps": {
                        "workspaceRoot": raw_workspace,
                        "executionRecovery": {"action": "retry_current_failure", "sourceRunId": source_run_id},
                    }},
                )]
            self.assertFalse(any('"type":"RUN_ERROR"' in frame for frame in frames), "".join(frames))
            attempts = await list_recovery_attempts_from_source(raw_workspace, source_run_id)
            self.assertEqual(len(attempts), 1)
            child_record = load_build_execution_record(state, attempts[0].new_run_id)
            assert child_record is not None
            self.assertEqual(calls, Counter({"A": 1, "B": 2}))
            self.assertEqual(child_record["build_run_id"], source_record["build_run_id"])
            if os.environ.get("BUILD_RECOVERY_EVIDENCE"):
                print(json.dumps({
                    "scenario": "business_retry", "source": source_run_id,
                    "child": attempts[0].new_run_id,
                    "entry_checkpoint": reentry.context_authority.checkpoint_id,
                    "build_run": source_record["build_run_id"],
                    "progress_source": child_record["progress_source_run_id"],
                    "calls": dict(calls),
                }, ensure_ascii=False))

    async def test_consecutive_recovery_inherits_progress_before_new_batch(self) -> None:
        """R2 在新结果提交前逃逸，R3 仍沿真实恢复父链领取 R1 的完成事实。"""

        await self._exercise_consecutive_recovery(exit_before_record=False)

    async def test_started_retry_interrupted_after_prepare(self) -> None:
        """R2 已提交 B 的重试准备后中断，R3 继续同一轮而不重复计数。"""

        await self._exercise_consecutive_recovery(exit_before_record=False, interrupt_r2=True)

    async def test_started_retry_interrupted_before_record(self) -> None:
        """R2 在建记录前中断，R3 依据同一 Build fork 的 RecoveryAttempt 重做准备。"""

        await self._exercise_consecutive_recovery(exit_before_record=True, interrupt_r2=True)

    async def test_consecutive_recovery_inherits_progress_before_child_record(self) -> None:
        """R2 在 Build 创建记录前退出，R3 沿父链找到同一调用的 R1 进度。"""

        await self._exercise_consecutive_recovery(exit_before_record=True)

    async def _exercise_consecutive_recovery(
        self, *, exit_before_record: bool, interrupt_r2: bool = False,
    ) -> None:
        """分别模拟 child 领取进度前后退出，再走真实 Native Recovery。"""

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            thread_id = "build-chain-thread"
            lifecycle = create_application_lifecycle(
                application_id="build-chain-app", application_name="Build chain recovery",
            )
            write_application_lifecycle(workspace, lifecycle.model_copy(update={
                "initialization": lifecycle.initialization.model_copy(update={
                    "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    "status": ApplicationLifecycleStatus.COMPLETED,
                }),
            }))
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
                "tasks": tasks, "active_run_id": "chain-R1", "active_thread_id": thread_id,
            })
            calls: Counter[str] = Counter()
            calls_lock = Lock()
            pre_record_escape_pending = exit_before_record

            def build_node(current: ProjectState) -> dict:
                """使用节点内恢复上下文运行生产 Build 调度器。"""

                nonlocal pre_record_escape_pending
                if pre_record_escape_pending and current.get("active_run_id") != "chain-R1":
                    pre_record_escape_pending = False
                    raise RuntimeError("R2 escaped")
                with bind_node_recovery(current, "build"):
                    return run_build_scheduler(current)

            def runner(**kwargs):
                """只让 B 的首次真实派发产生网络失败。"""

                results = []
                for task in kwargs["tasks"]:
                    with calls_lock:
                        calls[task["id"]] += 1
                        attempt = calls[task["id"]]
                    if task["id"] == "B" and attempt == 1:
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
            start_workbench_execution(
                workspace, scope="application", target_id="application", page_id=None,
                thread_id=thread_id, run_id="chain-R1", phase="build",
            )
            await observe_execution_started(
                workspace=raw_workspace, project_id=None, thread_id=thread_id,
                run_id="chain-R1", workflow_scope="application", first_node="build",
            )
            await observe_node_started(
                workspace=raw_workspace, run_id="chain-R1", thread_id=thread_id,
                workflow_scope="application", node_name="build",
            )
            with patch("app.graph.subgraphs.build.generate_data_sources_with_deep_agent", side_effect=runner):
                failed = await graph.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
            self.assertEqual(failed["status"], "failed")
            r1_record = load_build_execution_record(state, "chain-R1")
            assert r1_record is not None
            self.assertEqual(calls, Counter({"A": 1, "B": 1}))
            fail_workflow_lifecycle(raw_workspace, run_id="chain-R1", phase="build", error=RuntimeError("Build failed"))
            await observe_execution_failed(
                workspace=raw_workspace, run_id="chain-R1", thread_id=thread_id,
                workflow_scope="application", authoritative_node="build",
            )
            r1 = await get_execution(raw_workspace, "chain-R1")
            assert r1 is not None
            r2_plan = await BusinessTargetResolver().resolve(workspace=raw_workspace, source=r1, graph=graph)
            r2 = await prepare_native_recovery(
                workspace=raw_workspace, source_run_id="chain-R1", graph=graph, reentry_plan=r2_plan,
            )
            build_module = importlib.import_module("app.graph.subgraphs.build")
            try:
                with (
                    self.assertRaisesRegex(RuntimeError, "R2 escaped") as raised,
                    bind_recovery_runtime(r2.node_recovery_context()),
                    patch("app.graph.subgraphs.build.generate_data_sources_with_deep_agent", side_effect=runner),
                    patch.object(
                        build_module,
                        "_execute_ready_tasks" if interrupt_r2 else "_apply_scheduler_results",
                        side_effect=RuntimeError("R2 escaped"),
                    ),
                ):
                    await graph.ainvoke(None, config=r2.fork_config)
            finally:
                await stop_execution_heartbeat(r2.heartbeat_task)
            r2_record = load_build_execution_record(state, r2.new_run_id)
            if exit_before_record:
                self.assertIsNone(r2_record)
            else:
                assert r2_record is not None
                self.assertEqual(r2_record["execution_run_id"], r2.new_run_id)
                self.assertEqual(r2_record["progress_source_run_id"], r2.new_run_id)
                self.assertEqual(r2_record["build_run_id"], r1_record["build_run_id"])
                self.assertEqual(r2_record["execution_stage"], "prepared")
                prepared_tasks = {task["id"]: task for task in r2_record["progress"]["tasks"]}
                self.assertEqual(prepared_tasks["A"]["status"], "completed")
                self.assertEqual(prepared_tasks["B"]["status"], "pending")
                self.assertEqual(prepared_tasks["B"]["retry_count"], 1)
                self.assertEqual(
                    next(result for result in r2_record["progress"]["build_results"]
                         if result["task_id"] == "B")["status"], "failed",
                )
            if interrupt_r2:
                await mark_execution_interrupted(
                    workspace=workspace, run_id=r2.new_run_id,
                    interrupted_at=datetime.now(timezone.utc),
                )
            else:
                fail_workflow_lifecycle(raw_workspace, run_id=r2.new_run_id, phase="build", error=raised.exception)
                await observe_execution_failed(
                    workspace=raw_workspace, run_id=r2.new_run_id, thread_id=thread_id,
                    workflow_scope="application", exception=raised.exception, authoritative_node="build",
                )
            r2_source = await get_execution(raw_workspace, r2.new_run_id)
            assert r2_source is not None
            if interrupt_r2:
                self.assertEqual(r2_source.status, DurableExecutionStatus.INTERRUPTED)
                resolution = await InterruptedTargetResolver().resolve(
                    workspace=raw_workspace, source=r2_source, graph=graph,
                )
                self.assertEqual(resolution.kind, "continue")
                r3_plan = resolution.reentry_plan
                assert r3_plan is not None
                self.assertEqual(r3_plan.reason, WorkflowReentryReason.INTERRUPTED_CONTINUE)
            else:
                r3_plan = await FailureTargetResolver().resolve(
                    workspace=raw_workspace, source=r2_source, graph=graph,
                )
            if exit_before_record or interrupt_r2:
                with (
                    patch("app.protocols.execution_recovery.workflow_graph_for_request", return_value=graph),
                    patch("app.graph.subgraphs.build.generate_data_sources_with_deep_agent", side_effect=runner),
                    patch("app.graph.subgraphs.build._finalize_build", return_value=({}, {}, None, [], None)),
                ):
                    frames = [frame async for frame in build_execution_recovery_ag_ui_stream(
                        payload={"forwardedProps": {
                            "workspaceRoot": raw_workspace,
                            "executionRecovery": {
                                "action": "continue" if interrupt_r2 else "retry_current_failure",
                                "sourceRunId": r2.new_run_id,
                            },
                        }},
                    )]
                self.assertFalse(any('"type":"RUN_ERROR"' in frame for frame in frames), "".join(frames))
                attempts = await list_recovery_attempts_from_source(raw_workspace, r2.new_run_id)
                self.assertEqual(len(attempts), 1)
                r3_record = load_build_execution_record(state, attempts[0].new_run_id)
                assert r3_record is not None
                recovered_build_run_id = r3_record["build_run_id"]
            else:
                r3 = await prepare_native_recovery(
                    workspace=raw_workspace, source_run_id=r2.new_run_id,
                    graph=graph, reentry_plan=r3_plan,
                )
                self.assertEqual(r3.source_lineage_run_ids[:2], (r2.new_run_id, "chain-R1"))
                try:
                    with (
                        bind_recovery_runtime(r3.node_recovery_context()),
                        patch("app.graph.subgraphs.build.generate_data_sources_with_deep_agent", side_effect=runner),
                        patch("app.graph.subgraphs.build._finalize_build", return_value=({}, {}, None, [], None)),
                    ):
                        recovered = await graph.ainvoke(None, config=r3.fork_config)
                finally:
                    await stop_execution_heartbeat(r3.heartbeat_task)
                self.assertEqual(recovered["build_summary"]["status"], "completed")
                recovered_build_run_id = recovered["build_run_id"]
            self.assertEqual(recovered_build_run_id, r1_record["build_run_id"])
            self.assertEqual(calls, Counter({"A": 1, "B": 2 if (exit_before_record or interrupt_r2) else 3}))
            if os.environ.get("BUILD_RECOVERY_EVIDENCE"):
                print(json.dumps({
                    "scenario": (
                        "interrupt_before_record" if exit_before_record else "interrupt_after_prepare"
                    ) if interrupt_r2 else ("before_record" if exit_before_record else "before_batch_commit"),
                    "source": "chain-R1", "r2": r2.new_run_id,
                    "r3": attempts[0].new_run_id if (exit_before_record or interrupt_r2) else r3.new_run_id,
                    "r2_entry_checkpoint": r2_plan.context_authority.checkpoint_id,
                    "r3_entry_checkpoint": r3_plan.context_authority.checkpoint_id,
                    "build_run": r1_record["build_run_id"],
                    "r2_progress_source": r2_record["progress_source_run_id"] if r2_record else None,
                    "r2_execution_stage": r2_record["execution_stage"] if r2_record else None,
                    "r3_reason": r3_plan.reason.value,
                    "calls": dict(calls),
                }, ensure_ascii=False))
