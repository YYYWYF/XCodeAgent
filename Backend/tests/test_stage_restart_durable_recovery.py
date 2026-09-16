from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryAttemptStatus,
    RecoveryExecutionError,
    RecoveryPlan,
    RecoveryPoint,
    RecoveryPointKind,
    RecoverySourceAuthorityKind,
    RecoveryStrategy,
)
from app.persistence.execution_recovery import (
    claim_native_recovery_attempt,
    execution_recovery_db_path,
    get_execution,
    get_recovery_attempt,
    insert_execution,
    insert_recovery_point,
    update_recovery_attempt,
)
from app.services.execution_recovery_lineage import reconcile_recovery_attempt


class _GraphMustNotRun:
    """记录旧 Stage Restart 被拒绝时是否意外触发 Graph。"""

    def __init__(self) -> None:
        """初始化 Graph 调用计数器。"""

        self.calls = 0

    async def aget_state(self, _config: object) -> object:
        """拒绝任何旧 Stage Restart 读取 Graph state 的尝试。"""

        self.calls += 1
        raise AssertionError("legacy Stage Restart must not read Graph state")

    async def aupdate_state(self, _config: object, _updates: object) -> object:
        """拒绝任何旧 Stage Restart 写入 Graph state 的尝试。"""

        self.calls += 1
        raise AssertionError("legacy Stage Restart must not update Graph state")


class StageRestartRemovalTests(unittest.IsolatedAsyncioTestCase):
    """覆盖历史 Stage Restart durable row 的读取和 fail-closed 收口。"""

    def setUp(self) -> None:
        """为每个历史 row 场景创建隔离工作区。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)

    def tearDown(self) -> None:
        """释放隔离工作区。"""

        self._temporary_workspace.cleanup()

    async def test_legacy_stage_restart_attempt_fails_closed_without_graph(self) -> None:
        """历史 stage_restart attempt 只能进入 finalization failure，不能启动 Graph。"""

        source, native_plan = await self._prepare_source_and_plan()
        child, _lease, _attempt = await claim_native_recovery_attempt(
            source=source,
            plan=native_plan,
            new_run_id="legacy-stage-child",
            owner_backend_instance_id="backend-a",
            owner_pid=101,
            lease_ttl_seconds=30,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        self._rewrite_attempt_as_legacy_stage_restart(child.run_id)

        graph = _GraphMustNotRun()
        with self.assertRaises(RecoveryExecutionError) as raised:
            await reconcile_recovery_attempt(
                workspace=self.workspace,
                new_run_id=child.run_id,
                graph=graph,
            )

        self.assertEqual(raised.exception.code, "RECOVERY_STRATEGY_UNSUPPORTED")
        persisted_attempt = await get_recovery_attempt(self.workspace, child.run_id)
        persisted_child = await get_execution(self.workspace, child.run_id)
        self.assertIsNotNone(persisted_attempt)
        self.assertIsNotNone(persisted_child)
        assert persisted_attempt is not None
        assert persisted_child is not None
        self.assertEqual(
            persisted_attempt.source_authority_kind,
            RecoverySourceAuthorityKind.FORMAL_STAGE,
        )
        self.assertEqual(persisted_attempt.strategy, RecoveryStrategy.STAGE_RESTART)
        self.assertEqual(
            persisted_attempt.status,
            RecoveryAttemptStatus.FINALIZATION_FAILED,
        )
        self.assertEqual(persisted_child.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(graph.calls, 0)

    def _rewrite_attempt_as_legacy_stage_restart(self, new_run_id: str) -> None:
        """把当前测试 attempt 改写成旧 SQLite row 的 Stage Restart 形状。"""

        connection = sqlite3.connect(execution_recovery_db_path(self.workspace))
        try:
            connection.execute(
                """
                UPDATE recovery_attempts
                SET source_authority_kind = 'formal_stage',
                    source_authority_sha256 = ?,
                    source_stage = 'technical_planning',
                    source_lifecycle_revision = 7,
                    source_recovery_point_id = NULL,
                    source_checkpoint_id = NULL,
                    source_checkpoint_ns = '',
                    strategy = 'stage_restart'
                WHERE new_run_id = ?
                """,
                ("a" * 64, new_run_id),
            )
            connection.commit()
        finally:
            connection.close()

    async def _prepare_source_and_plan(
        self,
    ) -> tuple[DurableExecutionRecord, RecoveryPlan]:
        """创建一个可被改写为历史 Stage Restart row 的中断 source。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id="legacy-stage-source",
            thread_id="legacy-stage-thread",
            workspace=str(self.workspace),
            project_id="legacy-stage-project",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="technical_planning_generate",
            current_node="technical_planning_generate",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )
        await insert_execution(source)
        point = RecoveryPoint(
            recovery_point_id="legacy-stage-point",
            run_id=source.run_id,
            thread_id=source.thread_id,
            kind=RecoveryPointKind.CHECKPOINT,
            checkpoint_id="legacy-stage-checkpoint",
            checkpoint_ns="",
            graph_node="technical_planning_generate",
            next_nodes=["technical_planning_generate"],
            captured_at=now,
        )
        await insert_recovery_point(workspace=self.workspace, point=point)
        return source, RecoveryPlan(
            source_run_id=source.run_id,
            thread_id=source.thread_id,
            decision="ready_native",
            strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
            recovery_point_id=point.recovery_point_id,
            checkpoint_id=point.checkpoint_id,
            checkpoint_ns="",
            next_nodes=list(point.next_nodes),
            reason_code="TEST_NATIVE_RECOVERY",
            reason="test",
        )


__all__ = ["StageRestartRemovalTests"]
