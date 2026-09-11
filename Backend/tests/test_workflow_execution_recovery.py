from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.execution_recovery import DurableExecutionStatus, RecoveryPointKind
from app.persistence.execution_recovery import (
    get_execution,
    get_latest_recovery_point,
    list_recovery_points,
)
from app.services.execution_recovery import (
    best_effort_recovery_observation,
    capture_recovery_point,
    durable_execution_status,
    observe_execution_cancelled,
    observe_execution_failed,
    observe_execution_finished,
    observe_execution_started,
    observe_node_started,
)


class _SnapshotGraph:
    """提供真实 StateSnapshot 形状的最小异步 Graph double。"""

    def __init__(self, snapshot: SimpleNamespace) -> None:
        """保存测试期间由 aget_state 返回的快照。"""

        self.snapshot = snapshot

    async def aget_state(self, config: dict[str, object]) -> SimpleNamespace:
        """返回固定快照，模拟 LangGraph 的异步 checkpoint 查询。"""

        del config
        return self.snapshot


class WorkflowExecutionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """覆盖恢复服务的运行时边界、状态映射和 fail-open 语义。"""

    def setUp(self) -> None:
        """为每个运行时测试准备隔离工作区。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)

    def tearDown(self) -> None:
        """释放测试工作区。"""

        self._temporary_workspace.cleanup()

    async def test_application_planning_execution_is_recorded(self) -> None:
        """独立 application planning Graph 必须拥有自己的 execution kind。"""

        record = await observe_execution_started(
            workspace=str(self.workspace),
            project_id="app-001",
            thread_id="thread-001",
            run_id="run-planning",
            workflow_scope="application_planning",
            first_node="requirements",
        )
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.execution_kind, "application_planning")
        loaded = await get_execution(self.workspace, "run-planning")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.first_node, "requirements")

    async def test_capture_without_checkpoint_creates_entry_point(self) -> None:
        """测试 Graph 没有真实 checkpoint 时只能产生 ENTRY 现场。"""

        await observe_execution_started(
            workspace=str(self.workspace),
            project_id=None,
            thread_id="thread-001",
            run_id="run-entry",
            workflow_scope="page",
            first_node="A",
        )
        graph = _SnapshotGraph(
            SimpleNamespace(
                config={"configurable": {"thread_id": "thread-001"}},
                next=(),
                values={"phase": "A", "status": "running"},
            )
        )
        point = await capture_recovery_point(
            graph=graph,
            config={"configurable": {"thread_id": "thread-001"}},
            workspace=str(self.workspace),
            thread_id="thread-001",
            run_id="run-entry",
            workflow_scope="page",
            first_node="A",
        )
        self.assertIsNotNone(point)
        assert point is not None
        self.assertEqual(point.kind, RecoveryPointKind.ENTRY)
        self.assertIsNone(point.checkpoint_id)
        self.assertEqual(point.next_nodes, ["A"])

    async def test_capture_checkpoint_records_next_nodes_and_revisions(self) -> None:
        """真实快照字段必须完整投影为轻量 RecoveryPoint。"""

        await observe_execution_started(
            workspace=str(self.workspace),
            project_id=None,
            thread_id="thread-001",
            run_id="run-checkpoint",
            workflow_scope="page",
            first_node="A",
        )
        graph = _SnapshotGraph(
            SimpleNamespace(
                config={
                    "configurable": {
                        "thread_id": "thread-001",
                        "checkpoint_id": "cp-a",
                        "checkpoint_ns": "main",
                    }
                },
                next=("B",),
                values={
                    "phase": "A",
                    "status": "running",
                    "workspace_revision": "revision-123",
                    "workspace_snapshot_hash": "hash-123",
                },
            )
        )
        point = await capture_recovery_point(
            graph=graph,
            config={"configurable": {"thread_id": "thread-001"}},
            workspace=str(self.workspace),
            thread_id="thread-001",
            run_id="run-checkpoint",
            workflow_scope="page",
            completed_node="A",
        )
        self.assertIsNotNone(point)
        assert point is not None
        self.assertEqual(point.kind, RecoveryPointKind.CHECKPOINT)
        self.assertEqual(point.checkpoint_id, "cp-a")
        self.assertEqual(point.checkpoint_ns, "main")
        self.assertEqual(point.completed_node, "A")
        self.assertEqual(point.next_nodes, ["B"])
        self.assertEqual(point.workspace_revision, "revision-123")
        self.assertEqual(point.workspace_snapshot_hash, "hash-123")

    async def test_node_started_updates_current_node(self) -> None:
        """Node started 观测必须及时推进 Execution.currentNode。"""

        await observe_execution_started(
            workspace=str(self.workspace),
            project_id=None,
            thread_id="thread-001",
            run_id="run-node",
            workflow_scope="page",
            first_node="A",
        )
        await observe_node_started(
            workspace=str(self.workspace),
            run_id="run-node",
            thread_id="thread-001",
            workflow_scope="page",
            node_name="B",
        )
        record = await get_execution(self.workspace, "run-node")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.current_node, "B")
        self.assertEqual(record.status, DurableExecutionStatus.RUNNING)

    async def test_terminal_statuses_are_projected_without_lifecycle_calls(self) -> None:
        """失败、取消和等待确认只更新恢复库，不调用业务生命周期。"""

        await observe_execution_started(
            workspace=str(self.workspace),
            project_id=None,
            thread_id="thread-001",
            run_id="run-terminal",
            workflow_scope="page",
            first_node="A",
        )
        with patch("app.services.execution_recovery.load_application_lifecycle") as load:
            load.assert_not_called()
            await observe_execution_failed(
                workspace=str(self.workspace),
                run_id="run-terminal",
                thread_id="thread-001",
                workflow_scope="page",
            )
        record = await get_execution(self.workspace, "run-terminal")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, DurableExecutionStatus.FAILED)

        await observe_execution_started(
            workspace=str(self.workspace),
            project_id=None,
            thread_id="thread-001",
            run_id="run-cancelled",
            workflow_scope="page",
            first_node="A",
        )
        await observe_execution_cancelled(
            workspace=str(self.workspace),
            run_id="run-cancelled",
            thread_id="thread-001",
            workflow_scope="page",
        )
        cancelled = await get_execution(self.workspace, "run-cancelled")
        self.assertIsNotNone(cancelled)
        assert cancelled is not None
        self.assertEqual(cancelled.status, DurableExecutionStatus.CANCELLED)

        await observe_execution_started(
            workspace=str(self.workspace),
            project_id=None,
            thread_id="thread-001",
            run_id="run-awaiting",
            workflow_scope="application_planning",
            first_node="requirements",
        )
        await observe_execution_finished(
            workspace=str(self.workspace),
            run_id="run-awaiting",
            thread_id="thread-001",
            workflow_scope="application_planning",
            status=DurableExecutionStatus.AWAITING_USER,
        )
        awaiting = await get_execution(self.workspace, "run-awaiting")
        self.assertIsNotNone(awaiting)
        assert awaiting is not None
        self.assertEqual(awaiting.status, DurableExecutionStatus.AWAITING_USER)

    async def test_status_mapping_is_pure_and_complete(self) -> None:
        """状态映射覆盖等待、失败、取消、停止及正常完成分支。"""

        cases = (
            ({"status": "requires_user_input"}, DurableExecutionStatus.AWAITING_USER),
            ({"status": "failed"}, DurableExecutionStatus.FAILED),
            ({"status": "cancelled"}, DurableExecutionStatus.CANCELLED),
            ({"status": "stopped"}, DurableExecutionStatus.STOPPED),
            ({"status": "completed"}, DurableExecutionStatus.COMPLETED),
        )
        for result, expected in cases:
            self.assertEqual(
                durable_execution_status(result=result, summary={}),
                expected,
            )

    async def test_fail_open_logs_and_does_not_raise(self) -> None:
        """恢复库故障必须记录 warning，并让主流程继续。"""

        callback = AsyncMock(side_effect=OSError("database unavailable"))
        with self.assertLogs("uvicorn.error", level=logging.WARNING) as logs:
            result = await best_effort_recovery_observation(
                operation="execution.started",
                workspace=str(self.workspace),
                run_id="run-fail-open",
                thread_id="thread-001",
                workflow_scope="page",
                callback=callback,
            )
        self.assertIsNone(result)
        self.assertTrue(any("recovery.observation.failed" in message for message in logs.output))
        callback.assert_awaited_once()

    async def test_recovery_point_history_is_readable_after_terminal_status(self) -> None:
        """执行终态写入不能删除或覆盖之前的现场历史。"""

        await observe_execution_started(
            workspace=str(self.workspace),
            project_id=None,
            thread_id="thread-001",
            run_id="run-history",
            workflow_scope="page",
            first_node="A",
        )
        graph = _SnapshotGraph(
            SimpleNamespace(
                config={"configurable": {"checkpoint_id": "cp-a"}},
                next=("B",),
                values={"phase": "A", "status": "running"},
            )
        )
        await capture_recovery_point(
            graph=graph,
            config={},
            workspace=str(self.workspace),
            thread_id="thread-001",
            run_id="run-history",
            workflow_scope="page",
            completed_node="A",
        )
        await observe_execution_finished(
            workspace=str(self.workspace),
            run_id="run-history",
            thread_id="thread-001",
            workflow_scope="page",
            status=DurableExecutionStatus.COMPLETED,
        )
        self.assertEqual(len(await list_recovery_points(self.workspace, "run-history")), 1)
        latest = await get_latest_recovery_point(self.workspace, "run-history")
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(
            latest.checkpoint_id,
            "cp-a",
        )
