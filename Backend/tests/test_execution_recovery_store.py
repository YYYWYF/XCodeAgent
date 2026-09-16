from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionFailureEvidence,
    ExecutionFailureOrigin,
    RecoveryAttemptStatus,
    RecoveryLifecycleOwnershipMode,
)
from app.persistence.execution_recovery import (
    execution_recovery_db_path,
    get_execution,
    get_recovery_attempt,
    initialize_execution_recovery_store,
    insert_execution,
    update_execution_node,
    update_execution_status,
)


class ExecutionRecoveryStoreTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 Durable Execution、failure evidence 与 legacy schema 合同。"""

    def setUp(self) -> None:
        """为每个测试准备隔离工作区。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)

    def tearDown(self) -> None:
        """释放测试工作区及其中的 SQLite 文件。"""

        self._temporary_workspace.cleanup()

    async def test_initialize_creates_versioned_store(self) -> None:
        """fresh v9 只创建 canonical attempt columns，并删除 recovery_points。"""

        await initialize_execution_recovery_store(self.workspace)
        database_path = execution_recovery_db_path(self.workspace)
        self.assertTrue(database_path.exists())
        connection = sqlite3.connect(database_path)
        try:
            columns = [
                str(row[1])
                for row in connection.execute("PRAGMA table_info(recovery_attempts)")
            ]
            recovery_points = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'recovery_points'"
            ).fetchone()
            version = connection.execute(
                "SELECT value FROM recovery_meta WHERE key = 'schema_version'"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            columns,
            [
                "new_run_id", "source_run_id", "thread_id",
                "source_checkpoint_id", "source_checkpoint_ns",
                "source_lifecycle_revision", "lifecycle_ownership_mode",
                "status", "created_at", "handed_off_at", "started_at",
                "failed_at", "failure_code", "source_status",
                "source_failure_sha256",
            ],
        )
        self.assertIsNone(recovery_points)
        self.assertEqual(version[0] if version else None, "9")

    async def test_initialize_migrates_v8_native_checkpoint_attempt(self) -> None:
        """v8 native checkpoint HANDED_OFF row 必须无损迁移为 v9 transaction。"""

        database_path = execution_recovery_db_path(self.workspace)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE recovery_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO recovery_meta(key, value) VALUES ('schema_version', '8');
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
                    source_authority_kind TEXT NOT NULL,
                    source_authority_sha256 TEXT,
                    source_stage TEXT,
                    source_lifecycle_revision INTEGER,
                    source_recovery_point_id TEXT,
                    source_checkpoint_id TEXT,
                    source_checkpoint_ns TEXT NOT NULL DEFAULT '',
                    replay_checkpoint_id TEXT,
                    replay_checkpoint_ns TEXT NOT NULL DEFAULT '',
                    strategy TEXT NOT NULL,
                    lifecycle_ownership_mode TEXT NOT NULL DEFAULT 'source_owned',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    handed_off_at TEXT,
                    started_at TEXT,
                    failed_at TEXT,
                    failure_code TEXT,
                    source_status TEXT,
                    source_failure_sha256 TEXT
                );
                CREATE TABLE recovery_points (recovery_point_id TEXT PRIMARY KEY);
                """
            )
            connection.execute(
                """
                INSERT INTO execution_records VALUES (
                    'legacy-source', 'graph-thread', ?, 'project', 'workbench', 'page',
                    'A', 'A', 'interrupted', NULL, '2026-09-12T00:00:00+00:00',
                    '2026-09-12T00:00:01+00:00', '2026-09-12T00:00:01+00:00'
                )
                """,
                (str(self.workspace),),
            )
            connection.execute(
                """
                INSERT INTO execution_records VALUES (
                    'legacy-child', 'graph-thread', ?, 'project', 'workbench', 'page',
                    'A', 'A', 'running', NULL, '2026-09-12T00:00:02+00:00',
                    '2026-09-12T00:00:02+00:00', NULL
                )
                """,
                (str(self.workspace),),
            )
            connection.execute(
                """
                INSERT INTO recovery_attempts VALUES (
                    'legacy-child', 'legacy-source', 'graph-thread', 'checkpoint',
                    NULL, NULL, 7, NULL, 'legacy-checkpoint', '', NULL, '',
                    'native_checkpoint', 'source_owned', 'handed_off',
                    '2026-09-12T00:00:02+00:00',
                    '2026-09-12T00:00:03+00:00', NULL, NULL, NULL,
                    'interrupted', NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

        await initialize_execution_recovery_store(self.workspace)
        await initialize_execution_recovery_store(self.workspace)

        loaded = await get_execution(self.workspace, "legacy-source")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.thread_id, "graph-thread")
        self.assertIsNone(loaded.owner_session_id)
        attempt = await get_recovery_attempt(self.workspace, "legacy-child")
        self.assertIsNotNone(attempt)
        assert attempt is not None
        self.assertEqual(attempt.status, RecoveryAttemptStatus.HANDED_OFF)
        self.assertEqual(attempt.source_checkpoint_id, "legacy-checkpoint")
        self.assertEqual(attempt.source_lifecycle_revision, 7)
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
            boundary_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(node_entry_boundaries)")
            }
            version = connection.execute(
                "SELECT value FROM recovery_meta WHERE key = 'schema_version'"
            ).fetchone()
        finally:
            connection.close()
        self.assertIn("owner_session_id", execution_columns)
        self.assertNotIn("strategy", attempt_columns)
        self.assertNotIn("source_authority_kind", attempt_columns)
        self.assertEqual(
            {
                "boundary_id",
                "source_run_id",
                "thread_id",
                "target_node",
                "checkpoint_id",
                "checkpoint_ns",
                "captured_at",
            }
            - boundary_columns,
            set(),
        )
        self.assertEqual(version[0] if version else None, "9")

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

    async def test_insert_and_reload_execution_preserves_failure_diagnostic(self) -> None:
        """failure_json 应在关闭连接后完整 round-trip 安全诊断摘要。"""

        now = datetime.now(timezone.utc)
        record = self._record().model_copy(
            update={
                "status": DurableExecutionStatus.FAILED,
                "ended_at": now,
                "failure": ExecutionFailureEvidence(
                    origin=ExecutionFailureOrigin.MODEL_CALL,
                    code="MODEL_CONNECTION_ERROR",
                    operation="technical_planning",
                    model="mimo-v2.5-pro",
                    http_status=503,
                    replay_compatible=True,
                    diagnostic_message="model unavailable",
                ),
            }
        )

        await insert_execution(record)
        loaded = await get_execution(self.workspace, record.run_id)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertIsNotNone(loaded.failure)
        assert loaded.failure is not None
        self.assertEqual(loaded.failure.diagnostic_message, "model unavailable")

    async def test_old_failure_json_without_diagnostic_loads_as_none(self) -> None:
        """旧 failure_json 缺少新字段时应按当前模型默认值加载。"""

        record = self._record().model_copy(
            update={
                "status": DurableExecutionStatus.FAILED,
                "ended_at": datetime.now(timezone.utc),
                "failure": ExecutionFailureEvidence(
                    origin=ExecutionFailureOrigin.MODEL_CALL,
                    code="MODEL_CONNECTION_ERROR",
                    operation="technical_planning",
                    http_status=503,
                    replay_compatible=True,
                ),
            }
        )
        await insert_execution(record)
        legacy_failure = record.failure.model_dump(mode="json") if record.failure else {}
        legacy_failure.pop("diagnostic_message", None)
        database_path = execution_recovery_db_path(self.workspace)
        connection = sqlite3.connect(database_path)
        try:
            connection.execute(
                "UPDATE execution_records SET failure_json = ? WHERE run_id = ?",
                (json.dumps(legacy_failure), record.run_id),
            )
            connection.commit()
        finally:
            connection.close()

        loaded = await get_execution(self.workspace, record.run_id)

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertIsNotNone(loaded.failure)
        assert loaded.failure is not None
        self.assertIsNone(loaded.failure.diagnostic_message)

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
