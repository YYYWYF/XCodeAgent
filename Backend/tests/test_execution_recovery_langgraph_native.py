"""真实 LangGraph A→B→C Native Recovery replay 回归测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionLeaseStatus,
    RecoveryPlan,
    RecoveryPoint,
    RecoveryPointKind,
    RecoveryExecutionError,
    RecoveryStrategy,
    RecoveryAttemptStatus,
)
from app.persistence.execution_recovery import (
    claim_native_recovery_attempt,
    get_execution,
    get_execution_lease,
    get_recovery_attempt,
    initialize_execution_recovery_store,
    insert_execution,
    insert_recovery_point,
    list_recovery_points,
    update_recovery_attempt,
)
from app.services.backend_instance import current_backend_instance
from app.services.execution_recovery import capture_recovery_point
from app.services.execution_recovery_executor import (
    finalize_handed_off_recovery_attempt,
)
from app.services.execution_lease_heartbeat import stop_execution_heartbeat
from app.services.workspace_inspector import workspace_inventory


class ReplayState(TypedDict, total=False):
    """声明真实测试 Graph 使用的最小状态。"""

    active_run_id: str
    active_thread_id: str
    execution_log: list[str]
    observability: dict[str, Any]
    resume_from: str


def _build_replay_graph(counters: dict[str, int]) -> tuple[Any, InMemorySaver]:
    """构造带真实 checkpoint 的 A→B→C 图，并在 A 后停止首轮执行。"""

    builder = StateGraph(ReplayState)

    def node(name: str):
        """创建一个只追加执行日志的异步 Graph 节点。"""

        async def run(state: ReplayState) -> dict[str, Any]:
            """记录当前节点执行次数并返回完整日志。"""

            counters[name] += 1
            return {
                "execution_log": [*state.get("execution_log", []), name],
            }

        return run

    for name in ("A", "B", "C"):
        builder.add_node(name, node(name))
    builder.add_edge(START, "A")
    builder.add_edge("A", "B")
    builder.add_edge("B", "C")
    builder.add_edge("C", END)
    checkpointer = InMemorySaver()
    return builder.compile(checkpointer=checkpointer, interrupt_after=["A"]), checkpointer


class ExecutionRecoveryLangGraphNativeTests(unittest.IsolatedAsyncioTestCase):
    """验证 Native fork 从真实 A checkpoint 继续执行 B/C。"""

    async def test_real_state_graph_replay_does_not_repeat_completed_node(self) -> None:
        """真实 checkpoint fork 后 A 只执行一次，B/C 从 child 继续。"""

        counters = {"A": 0, "B": 0, "C": 0}
        graph, _checkpointer = _build_replay_graph(counters)
        thread_id = "langgraph-native-replay-thread"
        source_run_id = "langgraph-native-source"
        child_run_id = "langgraph-native-child"
        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            source_config = {"configurable": {"thread_id": thread_id}}
            await graph.ainvoke(
                {
                    "active_run_id": "old-run",
                    "active_thread_id": thread_id,
                    "execution_log": [],
                },
                config=source_config,
            )
            source_snapshot = await graph.aget_state(source_config)
            source_identity = source_snapshot.config["configurable"]
            self.assertEqual(tuple(source_snapshot.next), ("B",))
            source_checkpoint_id = str(source_identity["checkpoint_id"])
            source_checkpoint_ns = str(source_identity.get("checkpoint_ns") or "")

            captured_at = datetime.now(timezone.utc)
            source = DurableExecutionRecord(
                run_id=source_run_id,
                thread_id=thread_id,
                workspace=str(workspace),
                project_id=None,
                execution_kind="application_planning",
                workflow_scope="application_planning",
                first_node="A",
                current_node="B",
                status=DurableExecutionStatus.INTERRUPTED,
                started_at=captured_at,
                updated_at=captured_at,
                ended_at=captured_at,
            )
            await insert_execution(source)
            source_point = RecoveryPoint(
                recovery_point_id="langgraph-native-source-point",
                run_id=source_run_id,
                thread_id=thread_id,
                kind=RecoveryPointKind.CHECKPOINT,
                checkpoint_id=source_checkpoint_id,
                checkpoint_ns=source_checkpoint_ns,
                graph_node="A",
                completed_node="A",
                next_nodes=["B"],
                captured_at=source.updated_at,
            )
            await insert_recovery_point(workspace=workspace, point=source_point)
            plan = RecoveryPlan(
                source_run_id=source_run_id,
                thread_id=thread_id,
                decision="ready_native",
                strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
                recovery_point_id=source_point.recovery_point_id,
                checkpoint_id=source_checkpoint_id,
                checkpoint_ns=source_checkpoint_ns,
                next_nodes=["B"],
                reason_code="TEST",
                reason="real LangGraph replay",
            )
            identity = current_backend_instance()
            child, _lease, _attempt = await claim_native_recovery_attempt(
                source=source,
                plan=plan,
                new_run_id=child_run_id,
                owner_backend_instance_id=identity.instance_id,
                owner_pid=identity.pid,
                lease_ttl_seconds=60,
            )
            await update_recovery_attempt(
                workspace=workspace,
                new_run_id=child_run_id,
                status=RecoveryAttemptStatus.HANDED_OFF,
            )
            context = await finalize_handed_off_recovery_attempt(
                workspace=str(workspace),
                new_run_id=child_run_id,
                graph=graph,
            )
            await graph.ainvoke(None, config=context.fork_config)
            await stop_execution_heartbeat(context.heartbeat_task)

            child_config = context.observation_config
            history = [snapshot async for snapshot in graph.aget_state_history(child_config)]
            for completed_node in ("B", "C"):
                target_log = ["A", "B"] if completed_node == "B" else ["A", "B", "C"]
                target = next(
                    snapshot
                    for snapshot in history
                    if list(snapshot.values.get("execution_log", [])) == target_log
                )
                await capture_recovery_point(
                    graph=graph,
                    config=child_config,
                    workspace=str(workspace),
                    thread_id=thread_id,
                    run_id=child_run_id,
                    workflow_scope="application_planning",
                    completed_node=completed_node,
                    snapshot=target,
                )
            points = await list_recovery_points(workspace, child_run_id)
            source_after = await graph.aget_state(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": source_checkpoint_ns,
                        "checkpoint_id": source_checkpoint_id,
                    }
                }
            )
            attempt = await get_recovery_attempt(workspace, child_run_id)
            durable_child = await get_execution(workspace, child_run_id)

        self.assertEqual(counters, {"A": 1, "B": 1, "C": 1})
        self.assertEqual(source_after.values.get("active_run_id"), "old-run")
        self.assertEqual(context.fork_snapshot.values.get("active_run_id"), child_run_id)
        self.assertIsNotNone(attempt)
        assert attempt is not None
        self.assertEqual(attempt.status, RecoveryAttemptStatus.STARTED)
        self.assertIsNotNone(durable_child)
        assert durable_child is not None
        self.assertEqual(durable_child.status, DurableExecutionStatus.RUNNING)
        self.assertTrue(any(point.run_id == child_run_id and point.completed_node is None for point in points))
        self.assertTrue(any(point.completed_node == "B" and point.next_nodes == ["C"] for point in points))
        self.assertTrue(any(point.completed_node == "C" and point.next_nodes == [] for point in points))

    async def test_handed_off_restart_detects_workspace_drift_before_fork(self) -> None:
        """HANDED_OFF 重启遇到磁盘漂移时不能调用 aupdate_state。"""

        with tempfile.TemporaryDirectory() as raw_workspace:
            workspace = Path(raw_workspace)
            tracked_file = workspace / "tracked.txt"
            tracked_file.write_text("before", encoding="utf-8")
            await initialize_execution_recovery_store(workspace)
            _files, workspace_revision = workspace_inventory(workspace)
            captured_at = datetime.now(timezone.utc)
            source = DurableExecutionRecord(
                run_id="workspace-drift-source",
                thread_id="workspace-drift-thread",
                workspace=str(workspace),
                project_id=None,
                execution_kind="application_planning",
                workflow_scope="application_planning",
                first_node="A",
                current_node="B",
                status=DurableExecutionStatus.INTERRUPTED,
                started_at=captured_at,
                updated_at=captured_at,
                ended_at=captured_at,
            )
            await insert_execution(source)
            source_point = RecoveryPoint(
                recovery_point_id="workspace-drift-point",
                run_id=source.run_id,
                thread_id=source.thread_id,
                kind=RecoveryPointKind.CHECKPOINT,
                checkpoint_id="workspace-drift-checkpoint",
                checkpoint_ns="",
                graph_node="A",
                completed_node="A",
                next_nodes=["B"],
                workspace_revision=workspace_revision,
                captured_at=captured_at,
            )
            await insert_recovery_point(workspace=workspace, point=source_point)
            plan = RecoveryPlan(
                source_run_id=source.run_id,
                thread_id=source.thread_id,
                decision="ready_native",
                strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
                recovery_point_id=source_point.recovery_point_id,
                checkpoint_id=source_point.checkpoint_id,
                checkpoint_ns="",
                next_nodes=["B"],
                reason_code="TEST",
                reason="workspace drift",
            )
            identity = current_backend_instance()
            child, _lease, _attempt = await claim_native_recovery_attempt(
                source=source,
                plan=plan,
                new_run_id="workspace-drift-child",
                owner_backend_instance_id=identity.instance_id,
                owner_pid=identity.pid,
                lease_ttl_seconds=60,
            )
            await update_recovery_attempt(
                workspace=workspace,
                new_run_id=child.run_id,
                status=RecoveryAttemptStatus.HANDED_OFF,
            )
            tracked_file.write_text("after", encoding="utf-8")

            class NoForkGraph:
                """提供真实 checkpoint identity 读取但记录 fork 是否发生。"""

                def __init__(self) -> None:
                    """初始化 fork 调用记录。"""

                    self.fork_called = False

                async def aget_state(self, config: dict[str, Any]) -> Any:
                    """返回与 source RecoveryPoint 一致的 checkpoint 快照。"""

                    return SimpleNamespace(
                        config=config,
                        next=("B",),
                        tasks=(),
                        values={"active_run_id": source.run_id},
                    )

                async def aupdate_state(self, _config: dict[str, Any], _updates: dict[str, Any]) -> Any:
                    """记录任何不应发生的 fork 写入。"""

                    self.fork_called = True
                    raise AssertionError("workspace drift must block fork")

            graph = NoForkGraph()
            with self.assertRaises(RecoveryExecutionError) as raised:
                await finalize_handed_off_recovery_attempt(
                    workspace=str(workspace),
                    new_run_id=child.run_id,
                    graph=graph,
                )
            failed_attempt = await get_recovery_attempt(workspace, child.run_id)
            failed_child = await get_execution(workspace, child.run_id)
            failed_lease = await get_execution_lease(workspace, child.run_id)

        self.assertEqual(raised.exception.code, "WORKSPACE_DRIFT")
        self.assertFalse(graph.fork_called)
        self.assertIsNotNone(failed_attempt)
        self.assertIsNotNone(failed_child)
        self.assertIsNotNone(failed_lease)
        assert failed_attempt is not None
        assert failed_child is not None
        assert failed_lease is not None
        self.assertEqual(failed_attempt.status, RecoveryAttemptStatus.FINALIZATION_FAILED)
        self.assertEqual(failed_attempt.failure_code, "WORKSPACE_DRIFT")
        self.assertEqual(failed_child.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(failed_lease.status, ExecutionLeaseStatus.RELEASED)


__all__ = ["ExecutionRecoveryLangGraphNativeTests"]
