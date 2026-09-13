from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryAttemptAlreadyClaimedError,
    RecoveryAttemptStatus,
    RecoveryExecutionError,
    RecoveryPlan,
    RecoveryPoint,
    RecoveryPointKind,
    RecoverySourceAuthorityKind,
    RecoveryStrategy,
)
from app.persistence.execution_recovery import (
    claim_recovery_finalization,
    claim_stage_restart_attempt,
    claim_native_recovery_attempt,
    get_recovery_attempt,
    get_latest_recovery_point,
    insert_execution,
    insert_recovery_point,
    list_recovery_projection_candidates,
    update_execution_status,
    update_recovery_attempt,
)
from app.services.execution_recovery_lineage import (
    reconcile_recovery_attempt,
    resolve_recovery_lineage_head,
)


class StageRestartDurableRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 Stage Restart 纳入 Durable Recovery Transaction 后的核心不变量。"""

    def setUp(self) -> None:
        """为每个 Stage Restart 场景创建隔离工作区。"""

        self.temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_workspace.name)

    def tearDown(self) -> None:
        """释放隔离工作区。"""

        self.temporary_workspace.cleanup()

    async def test_a_stage_restart_uses_complete_transaction_state_machine(self) -> None:
        """Stage Restart 必须严格经过 PREPARING、HANDED_OFF、FINALIZING、STARTED。"""

        source = await self._insert_source("source-a")
        child, _lease, attempt = await self._claim_stage(source, "child-a")
        self.assertEqual(attempt.status, RecoveryAttemptStatus.PREPARING)
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        finalizing, _lease = await claim_recovery_finalization(
            workspace=self.workspace,
            new_run_id=child.run_id,
            new_owner_backend_instance_id="backend-b",
            new_owner_pid=202,
            lease_ttl_seconds=30,
        )
        self.assertEqual(finalizing.status, RecoveryAttemptStatus.FINALIZING)
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.STARTED,
        )
        persisted = await get_recovery_attempt(self.workspace, child.run_id)
        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertEqual(persisted.status, RecoveryAttemptStatus.STARTED)

    async def test_b_stage_restart_does_not_require_checkpoint(self) -> None:
        """FORMAL_STAGE attempt 允许 source checkpoint 和 RecoveryPoint 同时为空。"""

        source = await self._insert_source("source-b")
        child, _lease, attempt = await self._claim_stage(source, "child-b")
        self.assertEqual(attempt.source_authority_kind, RecoverySourceAuthorityKind.FORMAL_STAGE)
        self.assertIsNone(attempt.source_recovery_point_id)
        self.assertIsNone(attempt.source_checkpoint_id)
        self.assertEqual(child.first_node, "technical_planning_begin")

    async def test_c_distinct_recovery_children_preserve_lineage_identity(self) -> None:
        """连续 Native 与 Stage Restart child 必须拥有不同 runId 并形成唯一 lineage。"""

        source = await self._insert_source("source-c", status=DurableExecutionStatus.INTERRUPTED)
        point = RecoveryPoint(
            recovery_point_id="point-c",
            run_id=source.run_id,
            thread_id=source.thread_id,
            kind=RecoveryPointKind.CHECKPOINT,
            checkpoint_id="checkpoint-c",
            checkpoint_ns="",
            graph_node="technical_planning_begin",
            next_nodes=["technical_planning_begin"],
            captured_at=self._now(),
        )
        await insert_recovery_point(workspace=self.workspace, point=point)
        native_plan = RecoveryPlan(
            source_run_id=source.run_id,
            thread_id=source.thread_id,
            decision="ready_native",
            strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
            recovery_point_id=point.recovery_point_id,
            checkpoint_id=point.checkpoint_id,
            checkpoint_ns="",
            next_nodes=["technical_planning_begin"],
            reason_code="TEST",
            reason="test",
        )
        native_child, _lease, _attempt = await claim_native_recovery_attempt(
            source=source,
            plan=native_plan,
            new_run_id="child-c",
            owner_backend_instance_id="backend-a",
            owner_pid=101,
            lease_ttl_seconds=30,
        )
        await update_execution_status(
            workspace=self.workspace,
            run_id=native_child.run_id,
            status=DurableExecutionStatus.INTERRUPTED,
            ended=True,
        )
        refreshed_native_child = await self._execution(native_child.run_id)
        stage_child, _lease, _attempt = await self._claim_stage(
            refreshed_native_child,
            "child-c-stage",
        )
        resolution = await resolve_recovery_lineage_head(
            self.workspace,
            thread_id=source.thread_id,
            execution_kind="application_planning",
        )
        self.assertEqual(resolution.head.run_id if resolution.head else None, stage_child.run_id)
        self.assertNotEqual(source.run_id, native_child.run_id)
        self.assertNotEqual(native_child.run_id, stage_child.run_id)

    async def test_d_failed_stage_child_still_exposes_a_recovery_leaf(self) -> None:
        """Stage Restart 再次失败后，新的 lineage head 仍必须有恢复投影候选。"""

        source = await self._insert_source("source-d")
        child, _lease, _attempt = await self._claim_stage(source, "child-d")
        await update_execution_status(
            workspace=self.workspace,
            run_id=child.run_id,
            status=DurableExecutionStatus.INTERRUPTED,
            ended=True,
        )
        candidates = await list_recovery_projection_candidates(self.workspace)
        self.assertEqual([candidate.run_id for candidate in candidates], [child.run_id])

    async def test_e_stage_authority_is_persisted_as_action_identity(self) -> None:
        """Stage Restart 必须持久化正式 authority hash、stage 和 lifecycle revision。"""

        source = await self._insert_source("source-e")
        _child, _lease, attempt = await self._claim_stage(source, "child-e")
        self.assertEqual(attempt.source_authority_sha256, "a" * 64)
        self.assertEqual(attempt.source_stage, "technical_planning")
        self.assertEqual(attempt.source_lifecycle_revision, 7)

    async def test_f_preparing_crash_is_reconciled_without_second_child(self) -> None:
        """PREPARING 崩溃必须收口为 FAILED_PRESTART，不能隐式创建第二个 child。"""

        source = await self._insert_source("source-f")
        child, _lease, _attempt = await self._claim_stage(source, "child-f")
        reconciled = await reconcile_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
        )
        self.assertEqual(reconciled.status, RecoveryAttemptStatus.FAILED_PRESTART)
        self.assertIsNone(await get_recovery_attempt(self.workspace, "second-child-f"))

    async def test_g_handed_off_crash_can_be_taken_over_for_finalization(self) -> None:
        """HANDED_OFF 崩溃恢复必须可由新 Backend 接管为 FINALIZING。"""

        source = await self._insert_source("source-g")
        child, _lease, _attempt = await self._claim_stage(source, "child-g")
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        attempt, _lease = await claim_recovery_finalization(
            workspace=self.workspace,
            new_run_id=child.run_id,
            new_owner_backend_instance_id="backend-restarted",
            new_owner_pid=303,
            lease_ttl_seconds=30,
        )
        self.assertEqual(attempt.status, RecoveryAttemptStatus.FINALIZING)

    async def test_h_only_one_finalizer_can_hold_the_live_lease(self) -> None:
        """存活的 FINALIZING lease 必须阻止第二个 finalizer。"""

        source = await self._insert_source("source-h")
        child, _lease, _attempt = await self._claim_stage(source, "child-h")
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        await claim_recovery_finalization(
            workspace=self.workspace,
            new_run_id=child.run_id,
            new_owner_backend_instance_id="backend-first",
            new_owner_pid=404,
            lease_ttl_seconds=30,
        )
        with self.assertRaises(RecoveryExecutionError) as raised:
            await claim_recovery_finalization(
                workspace=self.workspace,
                new_run_id=child.run_id,
                new_owner_backend_instance_id="backend-second",
                new_owner_pid=405,
                lease_ttl_seconds=30,
            )
        self.assertEqual(raised.exception.code, "RECOVERY_FINALIZATION_ALREADY_CLAIMED")

    async def test_i_stage_entry_boundary_is_durable_before_started(self) -> None:
        """没有 Graph checkpoint 时也必须持久化真实 Technical Planning ENTRY boundary。"""

        source = await self._insert_source("source-i")
        child, _lease, _attempt = await self._claim_stage(source, "child-i")
        point = RecoveryPoint(
            recovery_point_id="entry-i",
            run_id=child.run_id,
            thread_id=child.thread_id,
            kind=RecoveryPointKind.ENTRY,
            checkpoint_id=None,
            checkpoint_ns="recovery-stage-child-i",
            graph_node="technical_planning_begin",
            next_nodes=["technical_planning_begin"],
            captured_at=self._now(),
        )
        await insert_recovery_point(workspace=self.workspace, point=point)
        stored = await get_latest_recovery_point(self.workspace, child.run_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.kind, RecoveryPointKind.ENTRY)
        self.assertIsNone(stored.checkpoint_id)

    async def test_j_double_claim_has_at_most_one_child(self) -> None:
        """并发执行相同 Stage Restart action 时最多允许一个 durable child。"""

        source = await self._insert_source("source-j")
        results = await asyncio.gather(
            self._claim_stage(source, "child-j-1"),
            self._claim_stage(source, "child-j-2"),
            return_exceptions=True,
        )
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], RecoveryAttemptAlreadyClaimedError)

    async def _claim_stage(
        self,
        source: DurableExecutionRecord,
        new_run_id: str,
    ) -> tuple[DurableExecutionRecord, object, object]:
        """使用固定正式 authority 创建一个 Stage Restart attempt。"""

        authority = SimpleNamespace(
            stage="technical_planning",
            authority_sha256="a" * 64,
            lifecycle_revision=7,
        )
        plan = RecoveryPlan(
            source_run_id=source.run_id,
            thread_id=source.thread_id,
            decision="requires_handler",
            strategy=RecoveryStrategy.STAGE_RESTART,
            next_nodes=["technical_planning_begin"],
            reason_code="TEST_STAGE_RESTART",
            reason="test",
            lifecycle_revision=7,
            source_authority_sha256=authority.authority_sha256,
            source_stage=authority.stage,
        )
        return await claim_stage_restart_attempt(
            source=source,
            plan=plan,
            authority=authority,
            new_run_id=new_run_id,
            owner_backend_instance_id="backend-a",
            owner_pid=101,
            lease_ttl_seconds=30,
        )

    async def _insert_source(
        self,
        run_id: str,
        *,
        status: DurableExecutionStatus = DurableExecutionStatus.FAILED,
    ) -> DurableExecutionRecord:
        """插入允许进入 Stage Restart claim 的 source execution。"""

        now = self._now()
        failure = None
        if status is DurableExecutionStatus.FAILED:
            from app.domain.execution_recovery import (
                ExecutionFailureEvidence,
                ExecutionFailureOrigin,
            )

            failure = ExecutionFailureEvidence(
                origin=ExecutionFailureOrigin.MODEL_CALL,
                code="MODEL_UNAVAILABLE",
                operation="technical_planning",
                replay_compatible=True,
            )
        record = DurableExecutionRecord(
            run_id=run_id,
            thread_id="stage-restart-thread",
            owner_session_id="stage-restart-session",
            workspace=str(self.workspace),
            project_id="app-stage-restart",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="technical_planning_begin",
            current_node="technical_planning_begin",
            status=status,
            started_at=now,
            updated_at=now,
            ended_at=now if status is not DurableExecutionStatus.RUNNING else None,
            failure=failure,
        )
        return await insert_execution(record)

    async def _execution(self, run_id: str) -> DurableExecutionRecord:
        """读取 claim 创建的 child execution。"""

        from app.persistence.execution_recovery import get_execution

        record = await get_execution(self.workspace, run_id)
        assert record is not None
        return record

    def _now(self) -> datetime:
        """返回测试所需的带 UTC 时区时间。"""

        return datetime.now(timezone.utc)


__all__ = ["StageRestartDurableRecoveryTests"]
