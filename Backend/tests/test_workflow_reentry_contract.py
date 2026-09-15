"""Workflow-wide Node Re-entry 的底层 Graph contract tests。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionFailureEvidence,
    ExecutionFailureOrigin,
    NodeEntryBoundary,
    RecoveryExecutionError,
    WorkflowReentryReason,
)
from app.persistence.execution_recovery import (
    get_node_entry_boundary,
    insert_node_entry_boundary,
    insert_execution,
)
from app.services.execution_recovery_executor import WorkflowReentryExecutor
from app.services.workflow_reentry import (
    FailureTargetResolver,
    RevisionTargetResolver,
    workflow_entry,
)


class ReentryState(TypedDict, total=False):
    """声明 generic Graph 使用的语义与运行字段。"""

    active_run_id: str
    active_thread_id: str
    mode: str
    revision_id: str
    change_request: str
    revision_impact: dict[str, Any]
    target: dict[str, Any]
    execution_log: list[str]


def _failure(node: str) -> ExecutionFailureEvidence:
    """构造不参与重入决策的异常证据。"""

    return ExecutionFailureEvidence(
        origin=ExecutionFailureOrigin.MODEL_CALL,
        code="MODEL_NOT_FOUND",
        operation=node,
        provider="deepseek",
        model="broken-model",
        http_status=404,
    )


def _source(
    *,
    workspace: Path,
    run_id: str,
    thread_id: str,
    current_node: str,
) -> DurableExecutionRecord:
    """构造已由异常收口的 generic FAILED execution。"""

    now = datetime.now(timezone.utc)
    return DurableExecutionRecord(
        run_id=run_id,
        thread_id=thread_id,
        workspace=str(workspace),
        execution_kind="workbench",
        workflow_scope="application",
        first_node="A",
        current_node=current_node,
        status=DurableExecutionStatus.FAILED,
        started_at=now,
        updated_at=now,
        ended_at=now,
        failure=_failure(current_node),
    )


class WorkflowReentryContractTests(unittest.IsolatedAsyncioTestCase):
    """验证首节点、中间节点、语义保存和 authority 损坏时的统一合同。"""

    async def test_first_node_failure_has_real_entry_checkpoint_and_fresh_binding(self) -> None:
        """首 Node 第一行失败后应从真实 entry checkpoint 重入并读取新 binding。"""

        calls: list[tuple[str, str, str]] = []
        binding = {"provider": "deepseek", "fail": True}

        async def node_a(state: ReentryState) -> dict[str, Any]:
            """记录当前 canonical provider，并按测试开关模拟首行失败。"""

            calls.append(("A", binding["provider"], str(state["active_run_id"])))
            if binding["fail"]:
                raise RuntimeError("provider 404")
            return {"execution_log": [*state.get("execution_log", []), "A"]}

        async def node_b(state: ReentryState) -> dict[str, Any]:
            """记录下游只在重试成功后执行。"""

            calls.append(("B", binding["provider"], str(state["active_run_id"])))
            return {"execution_log": [*state.get("execution_log", []), "B"]}

        builder = StateGraph(ReentryState)
        builder.add_node("workflow_entry", workflow_entry)
        builder.add_node("A", node_a)
        builder.add_node("B", node_b)
        builder.add_edge(START, "workflow_entry")
        builder.add_edge("workflow_entry", "A")
        builder.add_edge("A", "B")
        builder.add_edge("B", END)
        graph = builder.compile(checkpointer=InMemorySaver())

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            source = _source(
                workspace=workspace,
                run_id="source-first",
                thread_id="thread-first",
                current_node="A",
            )
            await insert_execution(source)
            with self.assertRaises(RuntimeError):
                await graph.ainvoke(
                    {
                        "active_run_id": source.run_id,
                        "active_thread_id": source.thread_id,
                        "mode": "revision",
                        "revision_id": "R1",
                        "change_request": "preserve me",
                        "revision_impact": {"earliest": "A"},
                        "target": {"type": "page", "id": "home"},
                        "execution_log": [],
                    },
                    config={"configurable": {"thread_id": source.thread_id}},
                )
            plan = await FailureTargetResolver().resolve(
                workspace=str(workspace),
                source=source,
                graph=graph,
            )
            boundary = await get_node_entry_boundary(
                workspace,
                source_run_id=source.run_id,
                thread_id=source.thread_id,
                target_node="A",
            )
            checkpoint = plan.context_authority
            source_snapshot = await graph.aget_state(
                {
                    "configurable": {
                        "thread_id": source.thread_id,
                        "checkpoint_ns": checkpoint.checkpoint_ns,
                        "checkpoint_id": checkpoint.checkpoint_id,
                    }
                }
            )
            binding.update({"provider": "mimo", "fail": False})
            fork_config = await graph.aupdate_state(
                source_snapshot.config,
                {
                    "active_run_id": "child-first",
                    "active_thread_id": source.thread_id,
                },
            )
            final = await graph.ainvoke(None, config=fork_config)

        self.assertEqual(plan.reason, WorkflowReentryReason.FAILURE_RETRY)
        self.assertEqual(source.current_node, "A")
        self.assertEqual(plan.target_node, "A")
        self.assertIsNotNone(boundary)
        assert boundary is not None
        self.assertEqual(boundary.target_node, "A")
        self.assertEqual(boundary.checkpoint_id, checkpoint.checkpoint_id)
        self.assertEqual(tuple(source_snapshot.next), ("A",))
        self.assertEqual(source_snapshot.values["revision_id"], "R1")
        self.assertEqual(source_snapshot.values["change_request"], "preserve me")
        self.assertEqual(source_snapshot.values["revision_impact"], {"earliest": "A"})
        self.assertEqual(source_snapshot.values["target"], {"type": "page", "id": "home"})
        self.assertEqual(calls, [("A", "deepseek", "source-first"), ("A", "mimo", "child-first"), ("B", "mimo", "child-first")])
        self.assertEqual(final["execution_log"], ["A", "B"])

    async def test_middle_node_failure_reenters_without_repeating_predecessor(self) -> None:
        """中间 Node 失败后 checkpoint.next 应只重入 B，A 不应再次执行。"""

        counters = {"A": 0, "B": 0, "C": 0}
        fail_b = {"value": True}
        b_contexts: list[tuple[str, str, dict[str, Any]]] = []

        async def node_a(state: ReentryState) -> dict[str, Any]:
            """记录上游 Node 的唯一一次成功。"""

            counters["A"] += 1
            return {"execution_log": [*state.get("execution_log", []), "A"]}

        async def node_b(state: ReentryState) -> dict[str, Any]:
            """第一次抛异常，重入后成功。"""

            counters["B"] += 1
            b_contexts.append(
                (
                    str(state.get("mode") or ""),
                    str(state.get("revision_id") or ""),
                    dict(state.get("target") or {}),
                )
            )
            if fail_b["value"]:
                raise RuntimeError("B failed")
            return {"execution_log": [*state.get("execution_log", []), "B"]}

        async def node_c(state: ReentryState) -> dict[str, Any]:
            """记录失败节点成功后的唯一下游执行。"""

            counters["C"] += 1
            return {"execution_log": [*state.get("execution_log", []), "C"]}

        builder = StateGraph(ReentryState)
        for name, node in (("A", node_a), ("B", node_b), ("C", node_c)):
            builder.add_node(name, node)
        builder.add_edge(START, "A")
        builder.add_edge("A", "B")
        builder.add_edge("B", "C")
        builder.add_edge("C", END)
        graph = builder.compile(checkpointer=InMemorySaver())

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            source = _source(
                workspace=workspace,
                run_id="source-middle",
                thread_id="thread-middle",
                current_node="B",
            )
            await insert_execution(source)
            with self.assertRaises(RuntimeError):
                await graph.ainvoke(
                    {
                        "active_run_id": source.run_id,
                        "active_thread_id": source.thread_id,
                        "mode": "revision",
                        "revision_id": "R-middle",
                        "target": {"type": "endpoint", "id": "orders"},
                        "execution_log": [],
                    },
                    config={"configurable": {"thread_id": source.thread_id}},
                )
            plan = await FailureTargetResolver().resolve(
                workspace=str(workspace),
                source=source,
                graph=graph,
            )
            authority = plan.context_authority
            fail_b["value"] = False
            fork_config = await graph.aupdate_state(
                {
                    "configurable": {
                        "thread_id": source.thread_id,
                        "checkpoint_ns": authority.checkpoint_ns,
                        "checkpoint_id": authority.checkpoint_id,
                    }
                },
                {"active_run_id": "child-middle"},
            )
            await graph.ainvoke(None, config=fork_config)

        self.assertEqual(plan.target_node, "B")
        self.assertEqual(counters, {"A": 1, "B": 2, "C": 1})
        self.assertEqual(
            b_contexts,
            [
                ("revision", "R-middle", {"type": "endpoint", "id": "orders"}),
                ("revision", "R-middle", {"type": "endpoint", "id": "orders"}),
            ],
        )

    async def test_corrupt_boundary_index_rebuilds_from_committed_history(self) -> None:
        """旁路索引损坏时必须回到同一 source 的真实 checkpoint history 重建。"""

        def node_a(_state: ReentryState) -> dict[str, Any]:
            """制造失败 Node，保留 workflow_entry 已提交的真实 predecessor。"""

            raise RuntimeError("A failed")

        builder = StateGraph(ReentryState)
        builder.add_node("workflow_entry", workflow_entry)
        builder.add_node("A", node_a)
        builder.add_edge(START, "workflow_entry")
        builder.add_edge("workflow_entry", "A")
        builder.add_edge("A", END)
        graph = builder.compile(checkpointer=InMemorySaver())

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            source = _source(
                workspace=workspace,
                run_id="source-corrupt-index",
                thread_id="thread-corrupt-index",
                current_node="A",
            )
            await insert_execution(source)
            with self.assertRaises(RuntimeError):
                await graph.ainvoke(
                    {
                        "active_run_id": source.run_id,
                        "active_thread_id": source.thread_id,
                        "mode": "initial",
                    },
                    config={"configurable": {"thread_id": source.thread_id}},
                )

            first_plan = await FailureTargetResolver().resolve(
                workspace=str(workspace),
                source=source,
                graph=graph,
            )
            first_boundary = await get_node_entry_boundary(
                workspace,
                source_run_id=source.run_id,
                thread_id=source.thread_id,
                target_node="A",
            )
            assert first_boundary is not None
            await insert_node_entry_boundary(
                workspace=workspace,
                boundary=NodeEntryBoundary(
                    boundary_id="corrupt-boundary-index",
                    source_run_id=source.run_id,
                    thread_id=source.thread_id,
                    target_node="A",
                    checkpoint_id="missing-checkpoint",
                    checkpoint_ns="",
                    captured_at=datetime.now(timezone.utc),
                ),
            )

            rebuilt_plan = await FailureTargetResolver().resolve(
                workspace=str(workspace),
                source=source,
                graph=graph,
            )
            rebuilt_boundary = await get_node_entry_boundary(
                workspace,
                source_run_id=source.run_id,
                thread_id=source.thread_id,
                target_node="A",
            )

        self.assertEqual(
            rebuilt_plan.context_authority.checkpoint_id,
            first_plan.context_authority.checkpoint_id,
        )
        self.assertIsNotNone(rebuilt_boundary)
        assert rebuilt_boundary is not None
        self.assertEqual(rebuilt_boundary.checkpoint_id, first_boundary.checkpoint_id)
        self.assertNotEqual(rebuilt_boundary.boundary_id, "corrupt-boundary-index")

    async def test_missing_source_owned_checkpoint_fails_closed(self) -> None:
        """只有其他 run 的同节点 checkpoint 时不得回退到旧现场或 Stage Restart。"""

        def node_a(_state: ReentryState) -> dict[str, Any]:
            """执行无业务更新的测试节点。"""

            return {}

        builder = StateGraph(ReentryState)
        builder.add_node("A", node_a)
        builder.add_edge(START, "A")
        builder.add_edge("A", END)
        graph = builder.compile(checkpointer=InMemorySaver())
        await graph.ainvoke(
            {"active_run_id": "another-run"},
            config={"configurable": {"thread_id": "thread-missing"}},
        )
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            source = _source(
                workspace=workspace,
                run_id="source-missing",
                thread_id="thread-missing",
                current_node="A",
            )
            await insert_execution(source)
            with self.assertRaises(RecoveryExecutionError) as raised:
                await FailureTargetResolver().resolve(
                    workspace=str(workspace),
                    source=source,
                    graph=graph,
                )

        self.assertEqual(raised.exception.code, "NODE_ENTRY_AUTHORITY_MISSING")

    def test_revision_context_uses_shared_executor_without_reanalysis(self) -> None:
        """Revision target/context 由调用方确定，共享 Executor 只覆盖 execution identity。"""

        state = {
            "mode": "revision",
            "revision_id": "R1",
            "change_request": "keep request",
            "revision_impact": {"earliest": "A"},
            "target": {"type": "endpoint", "id": "orders"},
            "active_run_id": "coordinator-run",
            "active_thread_id": "revision-thread",
        }
        plan = RevisionTargetResolver().resolve(
            execution_kind="application_planning",
            target_node="A",
            thread_id="revision-thread",
            run_id="revision-child",
            semantic_state=state,
            lifecycle_revision=7,
        )
        materialized = WorkflowReentryExecutor().materialize_revision_context(
            plan=plan,
            semantic_state=state,
            child_run_id="revision-child",
        )

        self.assertEqual(materialized["revision_id"], "R1")
        self.assertEqual(materialized["change_request"], "keep request")
        self.assertEqual(materialized["revision_impact"], {"earliest": "A"})
        self.assertEqual(materialized["target"], {"type": "endpoint", "id": "orders"})
        self.assertEqual(materialized["active_run_id"], "revision-child")


__all__ = ["WorkflowReentryContractTests"]
