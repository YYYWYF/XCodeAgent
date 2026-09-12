from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryAttemptStatus,
    RecoveryLifecycleOwnershipMode,
    RecoveryPoint,
    RecoveryPointKind,
)
from app.persistence.execution_recovery import (
    execution_recovery_db_path,
    get_execution,
    get_recovery_attempt,
    get_latest_recovery_point,
    initialize_execution_recovery_store,
    insert_execution,
    insert_recovery_point,
    list_recovery_points,
    update_execution_node,
    update_execution_status,
)


class ExecutionRecoveryStoreTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 P0.1 恢复记录库的持久化、历史和幂等合同。"""

    def setUp(self) -> None:
        """为每个测试准备隔离工作区。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)

    def tearDown(self) -> None:
        """释放测试工作区及其中的 SQLite 文件。"""

        self._temporary_workspace.cleanup()

    async def test_initialize_creates_versioned_store(self) -> None:
        """初始化必须创建独立数据库并写入 schema version。"""

        await initialize_execution_recovery_store(self.workspace)
        self.assertTrue(execution_recovery_db_path(self.workspace).exists())

    async def test_initialize_migrates_v3_records_without_rebuilding_history(self) -> None:
        """v3/v4 恢复记录必须原地补字段并保留旧历史。"""

        database_path = execution_recovery_db_path(self.workspace)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE recovery_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO recovery_meta(key, value) VALUES ('schema_version', '3');
                CREATE TABLE execution_records (
                    run_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    project_id TEXT,
                    execution_kind TEXT NOT NULL,
                    workflow_scope TEXT,
                    first_node TEXT NOT NULL,
                    current_node TEXT,
                    status TEXT NOT NULL,
                    last_recovery_point_id TEXT,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    ended_at TEXT
                );
                CREATE TABLE recovery_attempts (
                    new_run_id TEXT PRIMARY KEY,
                    source_run_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    source_recovery_point_id TEXT NOT NULL,
                    source_checkpoint_id TEXT NOT NULL,
                    source_checkpoint_ns TEXT NOT NULL DEFAULT '',
                    replay_checkpoint_id TEXT,
                    replay_checkpoint_ns TEXT NOT NULL DEFAULT '',
                    strategy TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    handed_off_at TEXT,
                    started_at TEXT,
                    failed_at TEXT,
                    failure_code TEXT
                );
                """
            )
            connection.execute(
                """
                INSERT INTO execution_records VALUES (
                    'legacy-run', 'graph-thread', ?, 'project', 'workbench', 'page',
                    'A', 'A', 'interrupted', NULL, '2026-09-12T00:00:00+00:00',
                    '2026-09-12T00:00:01+00:00', '2026-09-12T00:00:01+00:00'
                )
                """,
                (str(self.workspace),),
            )
            connection.execute(
                """
                INSERT INTO recovery_attempts VALUES (
                    'legacy-child', 'legacy-run', 'graph-thread', 'legacy-point',
                    'legacy-checkpoint', '', NULL, '', 'native_checkpoint',
                    'preparing', '2026-09-12T00:00:02+00:00', NULL, NULL, NULL, NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        await initialize_execution_recovery_store(self.workspace)

        loaded = await get_execution(self.workspace, "legacy-run")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.thread_id, "graph-thread")
        self.assertIsNone(loaded.owner_session_id)
        attempt = await get_recovery_attempt(self.workspace, "legacy-child")
        self.assertIsNotNone(attempt)
        assert attempt is not None
        self.assertEqual(attempt.status, RecoveryAttemptStatus.PREPARING)
        self.assertEqual(
            attempt.lifecycle_ownership_mode,
            RecoveryLifecycleOwnershipMode.SOURCE_OWNED,
        )
        connection = sqlite3.connect(database_path)
        try:
            execution_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(execution_records)")
            }
            attempt_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(recovery_attempts)")
            }
            version = connection.execute(
                "SELECT value FROM recovery_meta WHERE key = 'schema_version'"
            ).fetchone()
        finally:
            connection.close()
        self.assertIn("owner_session_id", execution_columns)
        self.assertIn("lifecycle_ownership_mode", attempt_columns)
        self.assertEqual(version[0] if version else None, "5")

    async def test_insert_and_reload_execution(self) -> None:
        """ExecutionRecord 关闭连接后仍应能按 runId 重新读取。"""

        record = self._record()
        await insert_execution(record)
        loaded = await get_execution(self.workspace, record.run_id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.thread_id, record.thread_id)
        self.assertEqual(loaded.current_node, record.current_node)
        self.assertEqual(loaded.status, DurableExecutionStatus.RUNNING)
        self.assertEqual(loaded.owner_session_id, "session-owner")

    async def test_node_and_status_updates_preserve_end_time_contract(self) -> None:
        """节点更新只改 currentNode，明确终态更新才写 endedAt。"""

        record = self._record()
        await insert_execution(record)
        await update_execution_node(
            workspace=self.workspace,
            run_id=record.run_id,
            node_name="B",
        )
        await update_execution_status(
            workspace=self.workspace,
            run_id=record.run_id,
            status=DurableExecutionStatus.COMPLETED,
            ended=True,
        )
        loaded = await get_execution(self.workspace, record.run_id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.current_node, "B")
        self.assertEqual(loaded.status, DurableExecutionStatus.COMPLETED)
        self.assertIsNotNone(loaded.ended_at)

    async def test_recovery_point_updates_latest_pointer_in_same_store(self) -> None:
        """RecoveryPoint 写入后必须同步维护 Execution 的 latest 指针。"""

        record = self._record()
        await insert_execution(record)
        point = self._point("rp-a", "cp-a", "A", ["B"])
        persisted = await insert_recovery_point(
            workspace=self.workspace,
            point=point,
        )
        loaded = await get_execution(self.workspace, record.run_id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.last_recovery_point_id, persisted.recovery_point_id)
        latest = await get_latest_recovery_point(self.workspace, record.run_id)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.completed_node, "A")

    async def test_recovery_points_keep_a_history(self) -> None:
        """多次节点边界必须保留为可按时间顺序读取的历史。"""

        record = self._record()
        await insert_execution(record)
        await insert_recovery_point(
            workspace=self.workspace,
            point=self._point("rp-a", "cp-a", "A", ["B"]),
        )
        await insert_recovery_point(
            workspace=self.workspace,
            point=self._point("rp-b", "cp-b", "B", ["C"]),
        )
        points = await list_recovery_points(self.workspace, record.run_id)
        self.assertEqual([point.completed_node for point in points], ["A", "B"])
        latest = await get_latest_recovery_point(self.workspace, record.run_id)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.checkpoint_id, "cp-b")

    async def test_duplicate_recovery_point_is_idempotent(self) -> None:
        """同一现场使用不同随机 ID 重复写入时只能保留一条记录。"""

        record = self._record()
        await insert_execution(record)
        first = self._point("rp-first", "cp-a", "A", ["B"])
        duplicate = first.model_copy(update={"recovery_point_id": "rp-second"})
        stored_first = await insert_recovery_point(workspace=self.workspace, point=first)
        stored_duplicate = await insert_recovery_point(
            workspace=self.workspace,
            point=duplicate,
        )
        self.assertEqual(stored_duplicate.recovery_point_id, stored_first.recovery_point_id)
        self.assertEqual(len(await list_recovery_points(self.workspace, record.run_id)), 1)

    async def test_entry_point_can_exist_without_a_real_checkpoint(self) -> None:
        """没有真实 checkpoint 时只能保存 ENTRY，而不能伪造 checkpointId。"""

        record = self._record()
        await insert_execution(record)
        point = self._point("rp-entry", None, None, [record.first_node])
        point = point.model_copy(update={"kind": RecoveryPointKind.ENTRY})
        await insert_recovery_point(workspace=self.workspace, point=point)
        loaded = await get_latest_recovery_point(self.workspace, record.run_id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.kind, RecoveryPointKind.ENTRY)
        self.assertIsNone(loaded.checkpoint_id)

    def _record(self) -> DurableExecutionRecord:
        """构造测试使用的最小执行记录。"""

        now = datetime.now(timezone.utc)
        return DurableExecutionRecord(
            run_id="run-001",
            thread_id="thread-001",
            owner_session_id="session-owner",
            workspace=str(self.workspace),
            project_id="project-001",
            execution_kind="workbench",
            workflow_scope="page",
            first_node="A",
            current_node="A",
            status=DurableExecutionStatus.RUNNING,
            started_at=now,
            updated_at=now,
        )

    def _point(
        self,
        point_id: str,
        checkpoint_id: str | None,
        completed_node: str | None,
        next_nodes: list[str],
    ) -> RecoveryPoint:
        """构造测试使用的恢复现场，并让历史排序稳定可预测。"""

        captured_at = datetime.now(timezone.utc) + timedelta(
            seconds=len(next_nodes)
        )
        return RecoveryPoint(
            recovery_point_id=point_id,
            run_id="run-001",
            thread_id="thread-001",
            kind=RecoveryPointKind.CHECKPOINT if checkpoint_id else RecoveryPointKind.ENTRY,
            checkpoint_id=checkpoint_id,
            checkpoint_ns="",
            graph_node=completed_node or next_nodes[0],
            completed_node=completed_node,
            next_nodes=next_nodes,
            phase=completed_node,
            state_status="running",
            lifecycle_revision=3,
            workspace_revision="revision-123",
            workspace_snapshot_hash="hash-123",
            captured_at=captured_at,
        )
