from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.application_lifecycle import (
    ApplicationInitialization,
    ApplicationLifecycle,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionLeaseStatus,
    RecoveryAttempt,
    RecoveryAttemptAlreadyClaimedError,
    RecoveryAttemptStatus,
    RecoveryExecutionError,
    RecoveryLifecycleOwnershipMode,
    RecoveryPlan,
)
from app.persistence.execution_recovery import (
    claim_recovery_finalization,
    claim_native_recovery_attempt,
    execution_recovery_db_path,
    get_execution,
    get_execution_lease,
    get_recovery_attempt,
    initialize_execution_recovery_store,
    insert_execution,
    list_recovery_attempts_from_source,
    reconcile_orphaned_executions,
    takeover_pre_runtime_recovery_lease,
    update_recovery_attempt,
)
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    write_application_lifecycle,
)
from app.services.execution_recovery_executor import (
    _start_recovery_heartbeat,
    finalize_handed_off_recovery_attempt,
)
from app.services.execution_recovery_lineage import (
    reconcile_recovery_attempt,
    resolve_recovery_lineage_head,
)
from app.services.execution_lease_heartbeat import stop_execution_heartbeat


class ExecutionRecoveryAttemptTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 P0.3B attempt 原子 claim、pre-runtime takeover 和 scanner 豁免。"""

    def setUp(self) -> None:
        """为每个 attempt 测试创建隔离工作区。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)

    def tearDown(self) -> None:
        """释放测试工作区。"""

        self._temporary_workspace.cleanup()

    async def test_pre_runtime_takeover_assigns_current_backend_identity(self) -> None:
        """HANDED_OFF child 重启后只能由当前 Backend 身份接管 lease。"""

        source, plan = await self._prepare_source_and_plan()
        child, lease, _attempt = await claim_native_recovery_attempt(
            source=source,
            plan=plan,
            new_run_id="child-handoff",
            owner_backend_instance_id="backend-a",
            owner_pid=101,
            lease_ttl_seconds=30,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )

        taken = await takeover_pre_runtime_recovery_lease(
            workspace=self.workspace,
            new_run_id=child.run_id,
            expected_attempt_status={RecoveryAttemptStatus.HANDED_OFF},
            new_owner_backend_instance_id="backend-b",
            new_owner_pid=202,
            lease_ttl_seconds=60,
        )

        self.assertEqual(taken.owner_backend_instance_id, "backend-b")
        self.assertEqual(taken.owner_pid, 202)
        self.assertEqual(taken.status, ExecutionLeaseStatus.ACTIVE)
        child_loaded = await get_execution(self.workspace, child.run_id)
        self.assertIsNotNone(child_loaded)
        assert child_loaded is not None
        self.assertEqual(child_loaded.status, DurableExecutionStatus.RUNNING)
        self.assertEqual(child_loaded.owner_session_id, "session-owner")
        self.assertEqual(child_loaded.thread_id, source.thread_id)
        self.assertEqual(lease.owner_backend_instance_id, "backend-a")

    async def test_claim_persists_pre_ownership_mode(self) -> None:
        """RecoveryAttempt 必须持久化 pre-ownership 语义供重启 reconciliation 使用。"""

        source, plan = await self._prepare_source_and_plan()
        pre_ownership_plan = plan.model_copy(
            update={
                "lifecycle_ownership_mode": RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP,
            }
        )
        child, _lease, attempt = await claim_native_recovery_attempt(
            source=source,
            plan=pre_ownership_plan,
            new_run_id="child-pre-ownership",
            owner_backend_instance_id="backend-a",
            owner_pid=101,
            lease_ttl_seconds=30,
        )

        self.assertEqual(
            attempt.lifecycle_ownership_mode,
            RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP,
        )
        self.assertEqual(attempt.source_checkpoint_id, plan.checkpoint_id)
        self.assertNotIn("source_recovery_point_id", type(attempt).model_fields)
        persisted = await get_recovery_attempt(self.workspace, child.run_id)
        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertEqual(
            persisted.lifecycle_ownership_mode,
            RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP,
        )
        self.assertEqual(persisted.source_checkpoint_id, plan.checkpoint_id)

    async def test_pre_ownership_preparing_crash_reconciles_to_handed_off(self) -> None:
        """PREPARING crash 在 lifecycle 未漂移时必须凭 durable facts 完成 handoff。"""

        lifecycle, source, child, claimed = await self._prepare_pre_ownership_crash(
            child_run_id="pre-ownership-child-success",
        )
        lease_before = await get_execution_lease(self.workspace, child.run_id)

        reconciled = await reconcile_recovery_attempt(
            workspace=str(self.workspace),
            new_run_id=child.run_id,
            graph=None,
        )

        lifecycle_after = load_application_lifecycle(self.workspace)
        child_after = await get_execution(self.workspace, child.run_id)
        lease_after = await get_execution_lease(self.workspace, child.run_id)
        attempts = await list_recovery_attempts_from_source(
            self.workspace,
            source.run_id,
        )
        self.assertEqual(claimed.status, RecoveryAttemptStatus.PREPARING)
        self.assertEqual(
            claimed.lifecycle_ownership_mode,
            RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP,
        )
        self.assertEqual(claimed.source_lifecycle_revision, lifecycle.revision)
        self.assertEqual(child.status, DurableExecutionStatus.RUNNING)
        self.assertIsNotNone(lease_before)
        assert lease_before is not None
        self.assertEqual(lease_before.status, ExecutionLeaseStatus.ACTIVE)
        self.assertIsNotNone(reconciled)
        assert reconciled is not None
        self.assertEqual(reconciled.status, RecoveryAttemptStatus.HANDED_OFF)
        self.assertEqual(reconciled.source_lifecycle_revision, lifecycle.revision)
        self.assertIsNotNone(lifecycle_after)
        assert lifecycle_after is not None
        self.assertEqual(lifecycle_after.active_run_id, child.run_id)
        self.assertEqual(lifecycle_after.initialization.thread_id, source.thread_id)
        self.assertIsNotNone(child_after)
        self.assertIsNotNone(lease_after)
        assert child_after is not None
        assert lease_after is not None
        self.assertEqual(child_after.status, DurableExecutionStatus.RUNNING)
        self.assertEqual(lease_after.status, ExecutionLeaseStatus.ACTIVE)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].new_run_id, child.run_id)

    async def test_pre_ownership_preparing_crash_fails_closed_after_revision_drift(
        self,
    ) -> None:
        """PREPARING crash 遇到 lifecycle revision 漂移时必须失败并释放 child。"""

        lifecycle, source, child, claimed = await self._prepare_pre_ownership_crash(
            child_run_id="pre-ownership-child-drift",
        )
        drifted = lifecycle.model_copy(
            update={
                "revision": lifecycle.revision + 1,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        write_application_lifecycle(
            self.workspace,
            drifted,
            expected_revision=lifecycle.revision,
        )

        reconciled = await reconcile_recovery_attempt(
            workspace=str(self.workspace),
            new_run_id=child.run_id,
            graph=None,
        )

        lifecycle_after = load_application_lifecycle(self.workspace)
        child_after = await get_execution(self.workspace, child.run_id)
        lease_after = await get_execution_lease(self.workspace, child.run_id)
        self.assertEqual(claimed.status, RecoveryAttemptStatus.PREPARING)
        self.assertIsNotNone(reconciled)
        assert reconciled is not None
        self.assertEqual(reconciled.status, RecoveryAttemptStatus.FAILED_PRESTART)
        self.assertEqual(reconciled.failure_code, "RECOVERY_STATE_DRIFT")
        self.assertIsNotNone(child_after)
        self.assertIsNotNone(lease_after)
        assert child_after is not None
        assert lease_after is not None
        self.assertEqual(child_after.status, DurableExecutionStatus.FAILED)
        self.assertEqual(lease_after.status, ExecutionLeaseStatus.RELEASED)
        self.assertIsNotNone(lifecycle_after)
        assert lifecycle_after is not None
        self.assertNotEqual(lifecycle_after.active_run_id, child.run_id)
        self.assertEqual(lifecycle_after.active_run_id, source.run_id)

    async def test_started_attempt_cannot_be_taken_over_pre_runtime(self) -> None:
        """STARTED child 已进入 Graph replay 后必须拒绝 pre-runtime takeover。"""

        source, plan = await self._prepare_source_and_plan()
        child, _lease, _attempt = await claim_native_recovery_attempt(
            source=source,
            plan=plan,
            new_run_id="child-started",
            owner_backend_instance_id="backend-a",
            owner_pid=101,
            lease_ttl_seconds=30,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        await claim_recovery_finalization(
            workspace=self.workspace,
            new_run_id=child.run_id,
            new_owner_backend_instance_id="backend-a",
            new_owner_pid=101,
            lease_ttl_seconds=30,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.STARTED,
        )

        with self.assertRaises(RecoveryExecutionError) as raised:
            await takeover_pre_runtime_recovery_lease(
                workspace=self.workspace,
                new_run_id=child.run_id,
                expected_attempt_status={
                    RecoveryAttemptStatus.PREPARING,
                    RecoveryAttemptStatus.HANDED_OFF,
                },
                new_owner_backend_instance_id="backend-b",
                new_owner_pid=202,
                lease_ttl_seconds=60,
            )
        self.assertEqual(raised.exception.code, "RECOVERY_ATTEMPT_ALREADY_STARTED")

    async def test_finalize_handed_off_uses_current_backend_for_takeover(self) -> None:
        """HANDED_OFF reconcile 必须用当前 Backend 身份接管 finalization。"""

        source, plan = await self._prepare_source_and_plan()
        child, _lease, _attempt = await claim_native_recovery_attempt(
            source=source,
            plan=plan,
            new_run_id="child-finalize",
            owner_backend_instance_id="backend-a",
            owner_pid=101,
            lease_ttl_seconds=30,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        with (
            patch(
                "app.services.execution_recovery_executor.current_backend_instance",
                return_value=SimpleNamespace(instance_id="backend-b", pid=202),
            ),
            patch(
                "app.services.execution_recovery_executor._start_recovery_heartbeat",
                return_value=None,
            ),
            patch(
                "app.services.execution_recovery_executor._revalidate_finalizing_recovery",
                new=AsyncMock(),
            ),
            patch(
                "app.services.execution_recovery_executor._fork_and_start",
                new=AsyncMock(return_value="context"),
            ) as fork,
        ):
            result = await finalize_handed_off_recovery_attempt(
                workspace=str(self.workspace),
                new_run_id=child.run_id,
                graph=object(),
            )

        self.assertEqual(result, "context")
        fork.assert_awaited_once()
        taken = await get_execution_lease(self.workspace, child.run_id)
        self.assertIsNotNone(taken)
        assert taken is not None
        self.assertEqual(taken.owner_backend_instance_id, "backend-b")
        self.assertEqual(taken.owner_pid, 202)

    async def test_application_planning_missing_lifecycle_fails_before_fork(self) -> None:
        """Application Planning finalization 缺少 lifecycle 时不得创建 fork checkpoint。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id="planning-source",
            thread_id="planning-thread",
            owner_session_id="session-owner",
            workspace=str(self.workspace),
            project_id="app-1",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="technical_planning_begin",
            current_node="technical_planning_begin",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )
        await insert_execution(source)
        plan = RecoveryPlan(
            source_run_id=source.run_id,
            thread_id=source.thread_id,
            target_node="technical_planning_begin",
            checkpoint_id="planning-source-checkpoint",
            checkpoint_ns="",
            lifecycle_ownership_mode=RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP,
        )
        child, _lease, _attempt = await claim_native_recovery_attempt(
            source=source,
            plan=plan,
            new_run_id="planning-child",
            owner_backend_instance_id="backend-old",
            owner_pid=101,
            lease_ttl_seconds=30,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        graph = SimpleNamespace(
            aget_state=AsyncMock(
                return_value=SimpleNamespace(
                    config={
                        "configurable": {
                            "thread_id": source.thread_id,
                            "checkpoint_ns": "",
                            "checkpoint_id": "planning-source-checkpoint",
                        }
                    },
                    next=("technical_planning_begin",),
                    tasks=(),
                    values={"active_run_id": source.run_id},
                )
            )
        )
        settings = SimpleNamespace(execution_recovery_lease_ttl_seconds=30)
        with (
            patch(
                "app.services.execution_recovery_executor.current_backend_instance",
                return_value=SimpleNamespace(instance_id="backend-new", pid=202),
            ),
            patch("app.services.execution_recovery_executor.Settings.from_env", return_value=settings),
            patch(
                "app.services.execution_recovery_executor._start_recovery_heartbeat",
                return_value=None,
            ),
            patch(
                "app.services.execution_recovery_executor._fork_and_start",
                new=AsyncMock(),
            ) as fork,
        ):
            with self.assertRaises(RecoveryExecutionError) as raised:
                await finalize_handed_off_recovery_attempt(
                    workspace=str(self.workspace),
                    new_run_id=child.run_id,
                    graph=graph,
                )

        self.assertEqual(raised.exception.code, "RECOVERY_STATE_DRIFT")
        fork.assert_not_awaited()

    async def test_finalization_heartbeat_prevents_takeover_during_slow_revalidation(self) -> None:
        """慢速 revalidation 超过原 lease TTL 时，存活 owner 仍拒绝第二次 finalization。"""

        source, plan = await self._prepare_source_and_plan()
        child, _lease, _attempt = await claim_native_recovery_attempt(
            source=source,
            plan=plan,
            new_run_id="child-slow-finalization",
            owner_backend_instance_id="backend-old",
            owner_pid=101,
            lease_ttl_seconds=0.08,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        revalidation_started = asyncio.Event()
        release_revalidation = asyncio.Event()
        handed_off_heartbeat: list[asyncio.Task[None] | None] = []

        async def slow_revalidation(**_kwargs: object) -> None:
            """阻塞在 revalidation 内部，让测试观察 heartbeat 的续租行为。"""

            revalidation_started.set()
            await release_revalidation.wait()

        async def capture_fork(**kwargs: object) -> str:
            """记录交给 Runtime 的 heartbeat task，并跳过实际 checkpoint fork。"""

            handed_off_heartbeat.append(kwargs["heartbeat_task"])  # type: ignore[arg-type]
            return "context"

        settings = SimpleNamespace(
            execution_recovery_heartbeat_seconds=0.02,
            execution_recovery_lease_ttl_seconds=0.08,
        )
        with (
            patch(
                "app.services.execution_recovery_executor.current_backend_instance",
                return_value=SimpleNamespace(instance_id="backend-a", pid=201),
            ),
            patch("app.services.execution_recovery_executor.Settings.from_env", return_value=settings),
            patch(
                "app.services.execution_recovery_executor._revalidate_finalizing_recovery",
                new=slow_revalidation,
            ),
            patch(
                "app.services.execution_recovery_executor._fork_and_start",
                new=capture_fork,
            ),
        ):
            finalization = asyncio.create_task(
                finalize_handed_off_recovery_attempt(
                    workspace=str(self.workspace),
                    new_run_id=child.run_id,
                    graph=object(),
                )
            )
            try:
                await asyncio.wait_for(revalidation_started.wait(), timeout=1)
                await asyncio.sleep(0.12)
                with self.assertRaises(RecoveryExecutionError) as raised:
                    await claim_recovery_finalization(
                        workspace=self.workspace,
                        new_run_id=child.run_id,
                        new_owner_backend_instance_id="backend-b",
                        new_owner_pid=202,
                        lease_ttl_seconds=0.08,
                    )
                self.assertEqual(raised.exception.code, "RECOVERY_FINALIZATION_ALREADY_CLAIMED")
                lease = await get_execution_lease(self.workspace, child.run_id)
                self.assertIsNotNone(lease)
                assert lease is not None
                self.assertEqual(lease.owner_backend_instance_id, "backend-a")
                self.assertEqual(lease.status, ExecutionLeaseStatus.ACTIVE)
                self.assertGreater(lease.expires_at, datetime.now(timezone.utc))
                release_revalidation.set()
                self.assertEqual(await finalization, "context")
            finally:
                release_revalidation.set()
                await asyncio.gather(finalization, return_exceptions=True)
                for heartbeat_task in handed_off_heartbeat:
                    await stop_execution_heartbeat(heartbeat_task)

    async def test_expired_finalization_can_be_taken_over_after_owner_heartbeat_stops(self) -> None:
        """owner heartbeat 停止并过期后，新的 Backend 可以安全接管 FINALIZING。"""

        source, plan = await self._prepare_source_and_plan()
        child, _lease, _attempt = await claim_native_recovery_attempt(
            source=source,
            plan=plan,
            new_run_id="child-expired-finalization",
            owner_backend_instance_id="backend-old",
            owner_pid=101,
            lease_ttl_seconds=0.08,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        settings = SimpleNamespace(
            execution_recovery_heartbeat_seconds=0.02,
            execution_recovery_lease_ttl_seconds=0.08,
        )
        with patch("app.services.execution_recovery_executor.Settings.from_env", return_value=settings):
            await claim_recovery_finalization(
                workspace=self.workspace,
                new_run_id=child.run_id,
                new_owner_backend_instance_id="backend-a",
                new_owner_pid=201,
                lease_ttl_seconds=0.08,
            )
            heartbeat_task = _start_recovery_heartbeat(
                workspace=str(self.workspace),
                run_id=child.run_id,
                owner_backend_instance_id="backend-a",
            )
            await stop_execution_heartbeat(heartbeat_task)

        await asyncio.sleep(0.1)
        _attempt, lease = await claim_recovery_finalization(
            workspace=self.workspace,
            new_run_id=child.run_id,
            new_owner_backend_instance_id="backend-b",
            new_owner_pid=202,
            lease_ttl_seconds=0.08,
        )
        self.assertEqual(lease.owner_backend_instance_id, "backend-b")
        self.assertEqual(lease.owner_pid, 202)
        self.assertEqual(lease.status, ExecutionLeaseStatus.ACTIVE)

    async def test_finalization_failure_stops_heartbeat_and_releases_lease(self) -> None:
        """revalidation 失败后必须先停止 heartbeat，再释放 child lease。"""

        source, plan = await self._prepare_source_and_plan()
        child, _lease, _attempt = await claim_native_recovery_attempt(
            source=source,
            plan=plan,
            new_run_id="child-failed-finalization",
            owner_backend_instance_id="backend-old",
            owner_pid=101,
            lease_ttl_seconds=0.08,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )

        async def fail_revalidation(**_kwargs: object) -> None:
            """模拟 workspace drift，验证 finalization failure 的清理顺序。"""

            raise RecoveryExecutionError("WORKSPACE_DRIFT", "workspace drift")

        settings = SimpleNamespace(
            execution_recovery_heartbeat_seconds=0.02,
            execution_recovery_lease_ttl_seconds=0.08,
        )
        with (
            patch(
                "app.services.execution_recovery_executor.current_backend_instance",
                return_value=SimpleNamespace(instance_id="backend-a", pid=201),
            ),
            patch("app.services.execution_recovery_executor.Settings.from_env", return_value=settings),
            patch(
                "app.services.execution_recovery_executor._revalidate_finalizing_recovery",
                new=fail_revalidation,
            ),
        ):
            with self.assertRaises(RecoveryExecutionError) as raised:
                await finalize_handed_off_recovery_attempt(
                    workspace=str(self.workspace),
                    new_run_id=child.run_id,
                    graph=object(),
                )

        self.assertEqual(raised.exception.code, "WORKSPACE_DRIFT")
        failed_attempt = await get_recovery_attempt(self.workspace, child.run_id)
        failed_child = await get_execution(self.workspace, child.run_id)
        failed_lease = await get_execution_lease(self.workspace, child.run_id)
        self.assertIsNotNone(failed_attempt)
        self.assertIsNotNone(failed_child)
        self.assertIsNotNone(failed_lease)
        assert failed_attempt is not None
        assert failed_child is not None
        assert failed_lease is not None
        released_at = failed_lease.expires_at
        self.assertEqual(failed_attempt.status, RecoveryAttemptStatus.FINALIZATION_FAILED)
        self.assertEqual(failed_child.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(failed_lease.status, ExecutionLeaseStatus.RELEASED)
        await asyncio.sleep(0.06)
        final_lease = await get_execution_lease(self.workspace, child.run_id)
        self.assertIsNotNone(final_lease)
        assert final_lease is not None
        self.assertEqual(final_lease.status, ExecutionLeaseStatus.RELEASED)
        self.assertEqual(final_lease.expires_at, released_at)

    async def test_scanner_preserves_pre_runtime_attempt_but_interrupts_started_orphan(self) -> None:
        """Scanner 保留 PREPARING/HANDED_OFF，却继续收敛 STARTED orphan。"""

        source, plan = await self._prepare_source_and_plan()
        child, _lease, _attempt = await claim_native_recovery_attempt(
            source=source,
            plan=plan,
            new_run_id="child-scanner",
            owner_backend_instance_id="backend-old",
            owner_pid=101,
            lease_ttl_seconds=30,
        )
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        preserved = await reconcile_orphaned_executions(
            workspace=self.workspace,
            current_backend_instance_id="backend-new",
            locally_active_run_ids=set(),
            now=future,
        )
        self.assertEqual(preserved, [])
        running = await get_execution(self.workspace, child.run_id)
        self.assertIsNotNone(running)
        assert running is not None
        self.assertEqual(running.status, DurableExecutionStatus.RUNNING)

        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )
        preserved_handoff = await reconcile_orphaned_executions(
            workspace=self.workspace,
            current_backend_instance_id="backend-new",
            locally_active_run_ids=set(),
            now=future,
        )
        self.assertEqual(preserved_handoff, [])

        await claim_recovery_finalization(
            workspace=self.workspace,
            new_run_id=child.run_id,
            new_owner_backend_instance_id="backend-new",
            new_owner_pid=202,
            lease_ttl_seconds=60,
            claim_at=future,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.STARTED,
        )
        interrupted = await reconcile_orphaned_executions(
            workspace=self.workspace,
            current_backend_instance_id="backend-new",
            locally_active_run_ids=set(),
            now=future + timedelta(seconds=61),
        )
        self.assertEqual([record.run_id for record in interrupted], [child.run_id])
        interrupted_child = await get_execution(self.workspace, child.run_id)
        interrupted_lease = await get_execution_lease(self.workspace, child.run_id)
        self.assertIsNotNone(interrupted_child)
        self.assertIsNotNone(interrupted_lease)
        assert interrupted_child is not None
        assert interrupted_lease is not None
        self.assertEqual(interrupted_child.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(interrupted_lease.status, ExecutionLeaseStatus.EXPIRED)

    async def test_concurrent_finalization_claim_has_one_winner(self) -> None:
        """同一个 HANDED_OFF child 的 finalization claim 只能有一个 winner。"""

        source, plan = await self._prepare_source_and_plan()
        child, _lease, _attempt = await claim_native_recovery_attempt(
            source=source,
            plan=plan,
            new_run_id="child-finalization-race",
            owner_backend_instance_id="backend-a",
            owner_pid=101,
            lease_ttl_seconds=30,
        )
        await update_recovery_attempt(
            workspace=self.workspace,
            new_run_id=child.run_id,
            status=RecoveryAttemptStatus.HANDED_OFF,
        )

        async def claim(owner: str, pid: int):
            """尝试争抢同一个 durable finalization owner。"""

            try:
                return await claim_recovery_finalization(
                    workspace=self.workspace,
                    new_run_id=child.run_id,
                    new_owner_backend_instance_id=owner,
                    new_owner_pid=pid,
                    lease_ttl_seconds=60,
                )
            except Exception as exc:  # noqa: BLE001 - 断言唯一 winner
                return exc

        first, second = await asyncio.gather(
            claim("backend-a", 101),
            claim("backend-b", 202),
        )
        successes = [result for result in (first, second) if not isinstance(result, Exception)]
        failures = [result for result in (first, second) if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], RecoveryExecutionError)
        assert isinstance(failures[0], RecoveryExecutionError)
        self.assertEqual(failures[0].code, "RECOVERY_FINALIZATION_ALREADY_CLAIMED")
        durable_child = await get_execution(self.workspace, child.run_id)
        durable_lease = await get_execution_lease(self.workspace, child.run_id)
        self.assertIsNotNone(durable_child)
        self.assertIsNotNone(durable_lease)
        assert durable_child is not None
        assert durable_lease is not None
        self.assertEqual(durable_child.status, DurableExecutionStatus.RUNNING)
        self.assertEqual(durable_lease.status, ExecutionLeaseStatus.ACTIVE)

    async def test_two_concurrent_claims_leave_one_active_child(self) -> None:
        """同一个 source 的并发 recovery claim 只能成功一次。"""

        source, plan = await self._prepare_source_and_plan()

        async def claim(run_id: str):
            """尝试创建一个竞争中的 recovery child。"""

            try:
                return await claim_native_recovery_attempt(
                    source=source,
                    plan=plan,
                    new_run_id=run_id,
                    owner_backend_instance_id="backend-test",
                    owner_pid=123,
                    lease_ttl_seconds=30,
                )
            except Exception as exc:  # noqa: BLE001 - 断言唯一 claim 冲突
                return exc

        first, second = await asyncio.gather(claim("child-a"), claim("child-b"))
        successes = [result for result in (first, second) if not isinstance(result, Exception)]
        failures = [result for result in (first, second) if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], RecoveryAttemptAlreadyClaimedError)

    async def test_v8_incompatible_attempts_migrate_without_replay(
        self,
    ) -> None:
        """v8 非 checkpoint active row 收口，已 STARTED edge 则只保留 lineage。"""

        for (
            strategy,
            authority_kind,
            old_status,
            expected_status,
            child_status,
            lease_status,
            failure_code,
            keep_checkpoint,
        ) in (
            (
                "stage_restart",
                "checkpoint",
                "preparing",
                RecoveryAttemptStatus.FAILED_PRESTART,
                DurableExecutionStatus.FAILED,
                ExecutionLeaseStatus.RELEASED,
                "RECOVERY_STRATEGY_UNSUPPORTED",
                True,
            ),
            (
                "operation_retry",
                "checkpoint",
                "handed_off",
                RecoveryAttemptStatus.FINALIZATION_FAILED,
                DurableExecutionStatus.INTERRUPTED,
                ExecutionLeaseStatus.RELEASED,
                "RECOVERY_STRATEGY_UNSUPPORTED",
                True,
            ),
            (
                "native_checkpoint",
                "formal_stage",
                "finalizing",
                RecoveryAttemptStatus.FINALIZATION_FAILED,
                DurableExecutionStatus.INTERRUPTED,
                ExecutionLeaseStatus.RELEASED,
                "RECOVERY_STRATEGY_UNSUPPORTED",
                True,
            ),
            (
                "operation_retry",
                "formal_stage",
                "started",
                RecoveryAttemptStatus.STARTED,
                DurableExecutionStatus.RUNNING,
                ExecutionLeaseStatus.ACTIVE,
                None,
                False,
            ),
        ):
            with self.subTest(strategy=strategy, status=old_status):
                with tempfile.TemporaryDirectory() as raw_workspace:
                    workspace = Path(raw_workspace)
                    now = datetime.now(timezone.utc)
                    source = DurableExecutionRecord(
                        run_id=f"legacy-source-{strategy}-{old_status}",
                        thread_id=f"legacy-thread-{strategy}-{old_status}",
                        owner_session_id="legacy-session",
                        workspace=str(workspace),
                        project_id=None,
                        execution_kind="workbench",
                        workflow_scope=None,
                        first_node="B",
                        current_node="B",
                        status=DurableExecutionStatus.INTERRUPTED,
                        started_at=now,
                        updated_at=now,
                        ended_at=now,
                    )
                    await insert_execution(source)
                    plan = RecoveryPlan(
                        source_run_id=source.run_id,
                        thread_id=source.thread_id,
                        target_node="B",
                        checkpoint_id="legacy-checkpoint",
                        checkpoint_ns="",
                    )
                    child, _lease, _attempt = await claim_native_recovery_attempt(
                        source=source,
                        plan=plan,
                        new_run_id=f"legacy-child-{strategy}-{old_status}",
                        owner_backend_instance_id="legacy-backend",
                        owner_pid=101,
                        lease_ttl_seconds=30,
                    )
                    connection = sqlite3.connect(execution_recovery_db_path(workspace))
                    try:
                        connection.executescript(
                            """
                            DROP INDEX idx_recovery_attempts_source;
                            DROP INDEX idx_recovery_attempts_status;
                            DROP INDEX idx_recovery_attempts_active_source;
                            ALTER TABLE recovery_attempts RENAME TO recovery_attempts_v9_seed;
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
                            """
                        )
                        seed = connection.execute(
                            "SELECT * FROM recovery_attempts_v9_seed WHERE new_run_id = ?",
                            (child.run_id,),
                        ).fetchone()
                        assert seed is not None
                        connection.execute(
                            """
                            INSERT INTO recovery_attempts VALUES (
                                ?, ?, ?, ?, NULL, NULL, ?, NULL, ?, '', NULL, '',
                                ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?
                            )
                            """,
                            (
                                seed[0], seed[1], seed[2], authority_kind,
                                seed[5], seed[3] if keep_checkpoint else None,
                                strategy, seed[6], old_status,
                                seed[8], seed[13], seed[14],
                            ),
                        )
                        connection.execute("DROP TABLE recovery_attempts_v9_seed")
                        connection.execute(
                            "UPDATE recovery_meta SET value = '8' WHERE key = 'schema_version'"
                        )
                        connection.commit()
                    finally:
                        connection.close()

                    await initialize_execution_recovery_store(workspace)
                    decoded = await get_recovery_attempt(workspace, child.run_id)
                    self.assertIsNotNone(decoded)
                    assert decoded is not None
                    self.assertEqual(decoded.status, expected_status)
                    self.assertEqual(decoded.failure_code, failure_code)
                    migrated_child = await get_execution(workspace, child.run_id)
                    migrated_lease = await get_execution_lease(workspace, child.run_id)
                    self.assertEqual(migrated_child.status, child_status)
                    self.assertEqual(migrated_lease.status, lease_status)
                    if expected_status is RecoveryAttemptStatus.STARTED:
                        self.assertIsNone(decoded.source_checkpoint_id)
                        lineage = await resolve_recovery_lineage_head(
                            str(workspace),
                            thread_id=source.thread_id,
                            execution_kind=source.execution_kind,
                        )
                        self.assertEqual(lineage.head.run_id, child.run_id)

    async def _prepare_source_and_plan(self) -> tuple[DurableExecutionRecord, RecoveryPlan]:
        """写入可供 Native claim 使用的中断 source 与 checkpoint plan。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id="source-run",
            thread_id="recovery-thread",
            owner_session_id="session-owner",
            workspace=str(self.workspace),
            project_id=None,
            execution_kind="workbench",
            workflow_scope=None,
            first_node="B",
            current_node="B",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )
        await insert_execution(source)
        return source, RecoveryPlan(
            source_run_id=source.run_id,
            thread_id=source.thread_id,
            target_node="B",
            checkpoint_id="source-checkpoint",
            checkpoint_ns="",
        )

    async def _prepare_pre_ownership_crash(
        self,
        *,
        child_run_id: str,
    ) -> tuple[
        ApplicationLifecycle,
        DurableExecutionRecord,
        DurableExecutionRecord,
        RecoveryAttempt,
    ]:
        """创建 claim 已提交但 lifecycle 尚未 handoff 的真实 PRE_OWNERSHIP 现场。"""

        source_run_id = f"{child_run_id}-source"
        thread_id = f"{child_run_id}-thread"
        lifecycle = create_application_lifecycle(
            application_id=f"{child_run_id}-app",
            application_name="PRE_OWNERSHIP Crash",
            initialization_thread_id=thread_id,
            active_run_id=source_run_id,
        ).model_copy(
            update={
                "initialization": ApplicationInitialization(
                    stage=ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
                    status=ApplicationLifecycleStatus.AWAITING_USER,
                    threadId=thread_id,
                )
            }
        )
        lifecycle = write_application_lifecycle(
            self.workspace,
            lifecycle,
            expected_revision=0,
        )
        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id=source_run_id,
            thread_id=thread_id,
            owner_session_id="pre-ownership-session",
            workspace=str(self.workspace),
            project_id=lifecycle.application.id,
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="technical_planning_begin",
            current_node="technical_planning_begin",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )
        await insert_execution(source)
        plan = RecoveryPlan(
            source_run_id=source.run_id,
            thread_id=source.thread_id,
            target_node="technical_planning_begin",
            checkpoint_id=f"{child_run_id}-checkpoint",
            checkpoint_ns="",
            lifecycle_ownership_mode=RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP,
            lifecycle_revision=lifecycle.revision,
        )
        child, _lease, attempt = await claim_native_recovery_attempt(
            source=source,
            plan=plan,
            new_run_id=child_run_id,
            owner_backend_instance_id="backend-before-crash",
            owner_pid=101,
            lease_ttl_seconds=60,
        )
        return lifecycle, source, child, attempt


__all__ = ["ExecutionRecoveryAttemptTests"]
