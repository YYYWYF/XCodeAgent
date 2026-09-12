from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionLeaseStatus,
    RecoveryAttemptAlreadyClaimedError,
    RecoveryAttemptStatus,
    RecoveryExecutionError,
    RecoveryPlan,
    RecoveryPoint,
    RecoveryPointKind,
    RecoveryStrategy,
)
from app.persistence.execution_recovery import (
    claim_recovery_finalization,
    claim_native_recovery_attempt,
    get_execution,
    get_execution_lease,
    get_recovery_attempt,
    insert_execution,
    insert_recovery_point,
    reconcile_orphaned_executions,
    takeover_pre_runtime_recovery_lease,
    update_recovery_attempt,
)
from app.services.execution_recovery_executor import (
    _start_recovery_heartbeat,
    finalize_handed_off_recovery_attempt,
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
            replay_checkpoint_id="fork-checkpoint",
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
            replay_checkpoint_id="fork-checkpoint",
        )
        interrupted = await reconcile_orphaned_executions(
            workspace=self.workspace,
            current_backend_instance_id="backend-new",
            locally_active_run_ids=set(),
            now=future,
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
        point = RecoveryPoint(
            recovery_point_id="source-point",
            run_id=source.run_id,
            thread_id=source.thread_id,
            kind=RecoveryPointKind.CHECKPOINT,
            checkpoint_id="source-checkpoint",
            checkpoint_ns="",
            graph_node="B",
            next_nodes=["B"],
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
            next_nodes=["B"],
            reason_code="TEST",
            reason="test",
        )


__all__ = ["ExecutionRecoveryAttemptTests"]
