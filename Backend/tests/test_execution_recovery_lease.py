from __future__ import annotations

import asyncio
import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionRunConflictError,
    DurableExecutionStatus,
    ExecutionLease,
    ExecutionLeaseStatus,
    RecoveryPoint,
    RecoveryPointKind,
)
from app.persistence.execution_recovery import (
    finish_execution_and_release_lease,
    get_execution,
    get_execution_lease,
    insert_execution,
    insert_execution_with_lease,
    insert_recovery_point,
    list_recovery_points,
    mark_execution_interrupted,
    reconcile_orphaned_executions,
    renew_execution_lease,
)
from app.protocols.application_lifecycle import build_application_lifecycle_ag_ui_stream
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.protocols.workflow.run_control import build_workflow_cancellation_ag_ui_stream
from app.protocols.workflow.run_control import workflow_run_registry
from app.services.application_lifecycle import ensure_application_lifecycle
from app.services.execution_lease_heartbeat import maintain_execution_heartbeat
from app.services.execution_recovery import (
    observe_execution_cancelled,
    observe_execution_finished,
    observe_execution_started,
)
from app.services.execution_recovery_scanner import reconcile_workspace_recovery


class _CountingGraph:
    """提供冲突场景使用的最小 Graph，并记录是否真正进入执行。"""

    def __init__(self) -> None:
        """初始化 Graph 执行计数。"""

        self.astream_started = False

    async def astream(
        self,
        graph_input: object,
        *,
        config: dict[str, object],
        stream_mode: list[str],
        **kwargs: object,
    ):
        """如果被调用则记录真实 Graph 已经开始。"""

        del graph_input, config, stream_mode, kwargs
        self.astream_started = True
        yield ("updates", {"requirements": {"status": "completed"}})


class _BlockingGraph:
    """提供显式取消测试使用的真实阻塞窗口。"""

    def __init__(self) -> None:
        """初始化节点开始和释放事件。"""

        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def astream(
        self,
        graph_input: object,
        *,
        config: dict[str, object],
        stream_mode: list[str],
        **kwargs: object,
    ):
        """阻塞在 Graph 内，等待 workflow_run_registry 发出取消。"""

        del graph_input, config, stream_mode, kwargs
        self.started.set()
        await self.release.wait()
        yield ("updates", {"requirements": {"status": "completed"}})


class ExecutionRecoveryLeaseTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 P0.2 Execution identity、lease、heartbeat 和 scanner 合同。"""

    def setUp(self) -> None:
        """为每个测试准备独立的工作区和恢复数据库。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)

    def tearDown(self) -> None:
        """释放测试工作区。"""

        self._temporary_workspace.cleanup()

    async def test_execution_and_lease_are_atomically_created_and_reloaded(self) -> None:
        """真实启动必须同时持久化 RUNNING Execution 与 ACTIVE lease。"""

        record = await observe_execution_started(
            workspace=str(self.workspace),
            project_id="project-001",
            thread_id="thread-001",
            run_id="run-atomic",
            workflow_scope="page",
            first_node="A",
            backend_instance_id="backend-test",
            backend_pid=123,
            lease_ttl=30,
        )

        self.assertIsNotNone(record)
        loaded = await get_execution(self.workspace, "run-atomic")
        lease = await get_execution_lease(self.workspace, "run-atomic")
        self.assertIsNotNone(loaded)
        self.assertIsNotNone(lease)
        assert loaded is not None
        assert lease is not None
        self.assertEqual(loaded.status, DurableExecutionStatus.RUNNING)
        self.assertEqual(lease.status, ExecutionLeaseStatus.ACTIVE)
        self.assertEqual(lease.owner_backend_instance_id, "backend-test")
        self.assertEqual(lease.owner_pid, 123)
        self.assertEqual(lease.heartbeat_at, lease.acquired_at)
        self.assertGreater(lease.expires_at, lease.heartbeat_at)

    async def test_existing_run_id_is_a_structured_conflict_and_preserves_old_state(self) -> None:
        """重复启动同一 runId 必须失败且不能覆盖旧 Execution 或 lease。"""

        await observe_execution_started(
            workspace=str(self.workspace),
            project_id=None,
            thread_id="thread-old",
            run_id="run-conflict",
            workflow_scope="page",
            first_node="old-node",
            backend_instance_id="backend-old",
            backend_pid=321,
        )
        before = await get_execution(self.workspace, "run-conflict")
        before_lease = await get_execution_lease(self.workspace, "run-conflict")

        with self.assertRaises(DurableExecutionRunConflictError) as context:
            await observe_execution_started(
                workspace=str(self.workspace),
                project_id="project-new",
                thread_id="thread-new",
                run_id="run-conflict",
                workflow_scope="page",
                first_node="new-node",
                backend_instance_id="backend-new",
                backend_pid=654,
            )

        error = context.exception
        self.assertEqual(error.code, "DURABLE_EXECUTION_RUN_ID_CONFLICT")
        self.assertEqual(error.run_id, "run-conflict")
        self.assertEqual(error.existing_status, DurableExecutionStatus.RUNNING.value)
        self.assertEqual(error.existing_thread_id, "thread-old")
        self.assertEqual(await get_execution(self.workspace, "run-conflict"), before)
        self.assertEqual(await get_execution_lease(self.workspace, "run-conflict"), before_lease)

    async def test_terminal_run_id_cannot_be_reused(self) -> None:
        """即使旧 run 已经 INTERRUPTED/EXPIRED，也不能再次成为新 attempt。"""

        await observe_execution_started(
            workspace=str(self.workspace),
            project_id=None,
            thread_id="thread-old",
            run_id="run-interrupted",
            workflow_scope="page",
            first_node="A",
            backend_instance_id="backend-old",
            backend_pid=321,
        )
        await mark_execution_interrupted(
            workspace=self.workspace,
            run_id="run-interrupted",
            interrupted_at=datetime.now(timezone.utc),
        )

        with self.assertRaises(DurableExecutionRunConflictError) as context:
            await observe_execution_started(
                workspace=str(self.workspace),
                project_id=None,
                thread_id="thread-new",
                run_id="run-interrupted",
                workflow_scope="page",
                first_node="A",
            )

        self.assertEqual(context.exception.code, "DURABLE_EXECUTION_RUN_ID_CONFLICT")
        record = await get_execution(self.workspace, "run-interrupted")
        lease = await get_execution_lease(self.workspace, "run-interrupted")
        self.assertIsNotNone(record)
        self.assertIsNotNone(lease)
        assert record is not None
        assert lease is not None
        self.assertEqual(record.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(lease.status, ExecutionLeaseStatus.EXPIRED)

    async def test_concurrent_start_has_one_winner_and_one_conflict(self) -> None:
        """事务层必须在两个并发 preflight 都放行时仍只允许一个创建者成功。"""

        async def start(thread_id: str) -> object:
            """尝试用相同 runId 创建一个独立的 Execution Attempt。"""

            try:
                return await observe_execution_started(
                    workspace=str(self.workspace),
                    project_id=None,
                    thread_id=thread_id,
                    run_id="run-race",
                    workflow_scope="page",
                    first_node="A",
                    backend_instance_id=f"backend-{thread_id}",
                    backend_pid=123,
                )
            except Exception as exc:
                return exc

        outcomes = await asyncio.gather(start("thread-a"), start("thread-b"))
        successes = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
        conflicts = [
            outcome
            for outcome in outcomes
            if isinstance(outcome, DurableExecutionRunConflictError)
        ]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertIsNotNone(await get_execution_lease(self.workspace, "run-race"))

    async def test_heartbeat_renews_lease_without_touching_execution_updated_at(self) -> None:
        """续租只改变 liveness 时间，不改变业务 Execution.updatedAt。"""

        await observe_execution_started(
            workspace=str(self.workspace),
            project_id=None,
            thread_id="thread-heartbeat",
            run_id="run-heartbeat",
            workflow_scope="page",
            first_node="A",
            backend_instance_id="backend-heartbeat",
            backend_pid=123,
        )
        before = await get_execution(self.workspace, "run-heartbeat")
        before_lease = await get_execution_lease(self.workspace, "run-heartbeat")
        assert before is not None
        assert before_lease is not None
        heartbeat_at = before_lease.heartbeat_at + timedelta(seconds=5)
        expires_at = before_lease.expires_at + timedelta(seconds=5)

        renewed = await renew_execution_lease(
            workspace=self.workspace,
            run_id="run-heartbeat",
            owner_backend_instance_id="backend-heartbeat",
            heartbeat_at=heartbeat_at,
            expires_at=expires_at,
        )

        after = await get_execution(self.workspace, "run-heartbeat")
        after_lease = await get_execution_lease(self.workspace, "run-heartbeat")
        self.assertTrue(renewed)
        self.assertIsNotNone(after)
        self.assertIsNotNone(after_lease)
        assert after is not None
        assert after_lease is not None
        self.assertEqual(after.updated_at, before.updated_at)
        self.assertEqual(after.status, DurableExecutionStatus.RUNNING)
        self.assertEqual(after_lease.heartbeat_at, heartbeat_at)
        self.assertEqual(after_lease.expires_at, expires_at)

    async def test_completion_failure_and_awaiting_user_release_lease(self) -> None:
        """正常完成、失败和等待用户都必须释放当前 ACTIVE lease。"""

        cases = (
            ("completed", DurableExecutionStatus.COMPLETED),
            ("failed", DurableExecutionStatus.FAILED),
            ("awaiting", DurableExecutionStatus.AWAITING_USER),
        )
        for suffix, expected_status in cases:
            run_id = f"run-terminal-{suffix}"
            await observe_execution_started(
                workspace=str(self.workspace),
                project_id=None,
                thread_id="thread-terminal",
                run_id=run_id,
                workflow_scope="page",
                first_node="A",
                backend_instance_id="backend-terminal",
                backend_pid=123,
            )
            await observe_execution_finished(
                workspace=str(self.workspace),
                run_id=run_id,
                thread_id="thread-terminal",
                workflow_scope="page",
                status=expected_status,
                backend_instance_id="backend-terminal",
            )
            record = await get_execution(self.workspace, run_id)
            lease = await get_execution_lease(self.workspace, run_id)
            self.assertIsNotNone(record)
            self.assertIsNotNone(lease)
            assert record is not None
            assert lease is not None
            self.assertEqual(record.status, expected_status)
            self.assertEqual(lease.status, ExecutionLeaseStatus.RELEASED)
            self.assertIsNotNone(lease.released_at)

    async def test_explicit_and_external_cancel_have_distinct_statuses_and_release_lease(self) -> None:
        """显式用户 Stop 标记 CANCELLED，外部 task cancel 标记 INTERRUPTED。"""

        for suffix, explicit, expected_status in (
            ("explicit", True, DurableExecutionStatus.CANCELLED),
            ("external", False, DurableExecutionStatus.INTERRUPTED),
        ):
            run_id = f"run-cancel-{suffix}"
            await observe_execution_started(
                workspace=str(self.workspace),
                project_id=None,
                thread_id="thread-cancel",
                run_id=run_id,
                workflow_scope="page",
                first_node="A",
                backend_instance_id="backend-cancel",
                backend_pid=123,
            )
            await observe_execution_cancelled(
                workspace=str(self.workspace),
                run_id=run_id,
                thread_id="thread-cancel",
                workflow_scope="page",
                explicitly_cancelled=explicit,
                backend_instance_id="backend-cancel",
            )
            record = await get_execution(self.workspace, run_id)
            lease = await get_execution_lease(self.workspace, run_id)
            self.assertIsNotNone(record)
            self.assertIsNotNone(lease)
            assert record is not None
            assert lease is not None
            self.assertEqual(record.status, expected_status)
            self.assertEqual(lease.status, ExecutionLeaseStatus.RELEASED)

    async def test_registry_cancel_and_wait_marks_real_runtime_cancelled(self) -> None:
        """真实 workflow_run_registry.cancel_and_wait 必须写入 CANCELLED 而非 INTERRUPTED。"""

        graph = _BlockingGraph()
        run_id = "run-registry-cancel"
        runtime_task = asyncio.create_task(
            self._collect_workflow(graph, thread_id="thread-registry-cancel", run_id=run_id)
        )
        await asyncio.wait_for(graph.started.wait(), timeout=5)

        cancellation_frames = [
            frame
            async for frame in build_workflow_cancellation_ag_ui_stream(
                thread_id="thread-cancel-request",
                run_id="run-cancel-request",
                target_run_id=run_id,
                workspace=str(self.workspace),
            )
        ]
        with self.assertRaises(asyncio.CancelledError):
            await runtime_task

        record = await get_execution(self.workspace, run_id)
        lease = await get_execution_lease(self.workspace, run_id)
        self.assertIn('"status":"cancelled"', "".join(cancellation_frames))
        self.assertIsNotNone(record)
        self.assertIsNotNone(lease)
        assert record is not None
        assert lease is not None
        self.assertEqual(record.status, DurableExecutionStatus.CANCELLED)
        self.assertEqual(lease.status, ExecutionLeaseStatus.RELEASED)

    async def test_legacy_running_without_lease_is_interrupted_and_point_is_kept(self) -> None:
        """P0.1 的 RUNNING/no-lease 记录可被 scanner 修正且不删除 RecoveryPoint。"""

        record = self._record("run-legacy", thread_id="thread-legacy")
        await insert_execution(record)
        await insert_recovery_point(
            workspace=self.workspace,
            point=self._point("run-legacy", "legacy-point"),
        )

        result = await reconcile_workspace_recovery(
            self.workspace,
            locally_active_run_ids=set(),
            current_backend_instance_id="backend-current",
            now=datetime.now(timezone.utc),
        )

        self.assertEqual(result.interrupted_run_ids, ["run-legacy"])
        loaded = await get_execution(self.workspace, "run-legacy")
        points = await list_recovery_points(self.workspace, "run-legacy")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual([point.recovery_point_id for point in points], ["legacy-point"])

    async def test_old_backend_owner_is_interrupted_even_before_lease_expiry(self) -> None:
        """Backend 重启后旧 owner 的未来 lease 也必须收敛为 INTERRUPTED/EXPIRED。"""

        await self._insert_running(
            "run-old-owner",
            owner_backend_instance_id="backend-old",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        await reconcile_workspace_recovery(
            self.workspace,
            locally_active_run_ids=set(),
            current_backend_instance_id="backend-new",
            now=datetime.now(timezone.utc),
        )

        record = await get_execution(self.workspace, "run-old-owner")
        lease = await get_execution_lease(self.workspace, "run-old-owner")
        self.assertIsNotNone(record)
        self.assertIsNotNone(lease)
        assert record is not None
        assert lease is not None
        self.assertEqual(record.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(lease.status, ExecutionLeaseStatus.EXPIRED)

    async def test_current_active_run_is_renewed_even_when_lease_expired(self) -> None:
        """当前 Backend 仍持有 active run 时，过期 lease 不能被 scanner 误杀。"""

        now = datetime.now(timezone.utc)
        await self._insert_running(
            "run-active",
            owner_backend_instance_id="backend-current",
            expires_at=now - timedelta(seconds=1),
        )

        result = await reconcile_workspace_recovery(
            self.workspace,
            locally_active_run_ids={"run-active"},
            current_backend_instance_id="backend-current",
            lease_ttl_seconds=30,
            now=now,
        )

        record = await get_execution(self.workspace, "run-active")
        lease = await get_execution_lease(self.workspace, "run-active")
        self.assertEqual(result.interrupted_run_ids, [])
        self.assertIsNotNone(record)
        self.assertIsNotNone(lease)
        assert record is not None
        assert lease is not None
        self.assertEqual(record.status, DurableExecutionStatus.RUNNING)
        self.assertEqual(lease.status, ExecutionLeaseStatus.ACTIVE)
        self.assertGreater(lease.expires_at, now)

    async def test_current_inactive_unexpired_run_is_preserved(self) -> None:
        """当前 owner 的未过期 lease 在短暂注册竞态中不能被中断。"""

        now = datetime.now(timezone.utc)
        await self._insert_running(
            "run-registration-race",
            owner_backend_instance_id="backend-current",
            expires_at=now + timedelta(minutes=1),
        )

        await reconcile_workspace_recovery(
            self.workspace,
            locally_active_run_ids=set(),
            current_backend_instance_id="backend-current",
            now=now,
        )

        record = await get_execution(self.workspace, "run-registration-race")
        lease = await get_execution_lease(self.workspace, "run-registration-race")
        self.assertIsNotNone(record)
        self.assertIsNotNone(lease)
        assert record is not None
        assert lease is not None
        self.assertEqual(record.status, DurableExecutionStatus.RUNNING)
        self.assertEqual(lease.status, ExecutionLeaseStatus.ACTIVE)

    async def test_current_inactive_expired_run_is_interrupted(self) -> None:
        """当前 owner 且未注册的过期 lease 必须收敛为中断。"""

        now = datetime.now(timezone.utc)
        await self._insert_running(
            "run-expired",
            owner_backend_instance_id="backend-current",
            expires_at=now - timedelta(seconds=1),
        )

        await reconcile_workspace_recovery(
            self.workspace,
            locally_active_run_ids=set(),
            current_backend_instance_id="backend-current",
            now=now,
        )

        record = await get_execution(self.workspace, "run-expired")
        lease = await get_execution_lease(self.workspace, "run-expired")
        self.assertIsNotNone(record)
        self.assertIsNotNone(lease)
        assert record is not None
        assert lease is not None
        self.assertEqual(record.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(lease.status, ExecutionLeaseStatus.EXPIRED)

    async def test_scanner_completion_race_cannot_overwrite_completed(self) -> None:
        """scanner 读到 RUNNING 后若先完成，条件更新不能再写 INTERRUPTED。"""

        now = datetime.now(timezone.utc)
        await self._insert_running(
            "run-completion-race",
            owner_backend_instance_id="backend-current",
            expires_at=now - timedelta(seconds=1),
        )
        from app.persistence import execution_recovery as persistence

        original_mark = persistence.mark_execution_interrupted

        async def complete_before_mark(**kwargs: object):
            """模拟 scanner 读取后由另一协程先完成 Execution。"""

            await finish_execution_and_release_lease(
                workspace=self.workspace,
                run_id="run-completion-race",
                status=DurableExecutionStatus.COMPLETED,
                owner_backend_instance_id="backend-current",
                ended_at=now,
            )
            return await original_mark(**kwargs)

        with patch.object(
            persistence,
            "mark_execution_interrupted",
            side_effect=complete_before_mark,
        ):
            result = await reconcile_orphaned_executions(
                workspace=self.workspace,
                current_backend_instance_id="backend-current",
                locally_active_run_ids=set(),
                now=now,
            )

        record = await get_execution(self.workspace, "run-completion-race")
        lease = await get_execution_lease(self.workspace, "run-completion-race")
        self.assertEqual(result, [])
        self.assertIsNotNone(record)
        self.assertIsNotNone(lease)
        assert record is not None
        assert lease is not None
        self.assertEqual(record.status, DurableExecutionStatus.COMPLETED)
        self.assertEqual(lease.status, ExecutionLeaseStatus.RELEASED)

    async def test_late_heartbeat_cannot_revive_interrupted_execution(self) -> None:
        """scanner 过期后到达的旧 heartbeat 必须被 CAS 拒绝。"""

        await observe_execution_started(
            workspace=str(self.workspace),
            project_id=None,
            thread_id="thread-late-heartbeat",
            run_id="run-late-heartbeat",
            workflow_scope="page",
            first_node="A",
            backend_instance_id="backend-current",
            backend_pid=123,
        )
        interrupted_at = datetime.now(timezone.utc)
        await mark_execution_interrupted(
            workspace=self.workspace,
            run_id="run-late-heartbeat",
            interrupted_at=interrupted_at,
        )

        renewed = await renew_execution_lease(
            workspace=self.workspace,
            run_id="run-late-heartbeat",
            owner_backend_instance_id="backend-current",
            heartbeat_at=interrupted_at + timedelta(seconds=1),
            expires_at=interrupted_at + timedelta(seconds=30),
        )

        record = await get_execution(self.workspace, "run-late-heartbeat")
        lease = await get_execution_lease(self.workspace, "run-late-heartbeat")
        self.assertFalse(renewed)
        self.assertIsNotNone(record)
        self.assertIsNotNone(lease)
        assert record is not None
        assert lease is not None
        self.assertEqual(record.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(lease.status, ExecutionLeaseStatus.EXPIRED)

    async def test_heartbeat_db_failure_is_fail_open_and_retries_next_cycle(self) -> None:
        """heartbeat 写库失败只告警，并在下一周期继续尝试。"""

        sleep_count = 0

        async def fake_sleep(_interval: float) -> None:
            """让心跳测试快速经过两个失败周期后结束。"""

            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise asyncio.CancelledError

        renew = AsyncMock(side_effect=OSError("database unavailable"))
        with (
            patch("app.services.execution_lease_heartbeat.asyncio.sleep", side_effect=fake_sleep),
            patch("app.services.execution_lease_heartbeat.renew_execution_lease", renew),
            self.assertLogs("uvicorn.error", level=logging.WARNING) as logs,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await maintain_execution_heartbeat(
                    workspace=str(self.workspace),
                    run_id="run-heartbeat-failure",
                    backend_instance_id="backend-current",
                    interval_seconds=0.01,
                    lease_ttl_seconds=1,
                )

        self.assertEqual(renew.await_count, 2)
        self.assertTrue(any("recovery.heartbeat.failed" in item for item in logs.output))

    async def test_application_lifecycle_get_runs_scanner_without_changing_lifecycle_file(self) -> None:
        """lifecycle get 应惰性扫描旧执行，但不把恢复事实写回 lifecycle JSON。"""

        ensure_application_lifecycle(
            self.workspace,
            application_id="app-001",
            application_name="测试应用",
        )
        await self._insert_running(
            "run-lifecycle-old",
            owner_backend_instance_id="backend-old",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        lifecycle_before = self.workspace / ".xcodeagent" / "application-lifecycle.json"
        before_text = lifecycle_before.read_text(encoding="utf-8")

        frames = [
            frame
            async for frame in build_application_lifecycle_ag_ui_stream(
                payload={
                    "threadId": "thread-lifecycle-get",
                    "runId": "run-lifecycle-get",
                    "forwardedProps": {
                        "applicationLifecycle": {
                            "action": "get",
                            "workspaceRoot": str(self.workspace),
                        }
                    },
                }
            )
        ]

        loaded = await get_execution(self.workspace, "run-lifecycle-old")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(lifecycle_before.read_text(encoding="utf-8"), before_text)
        self.assertTrue(any('"type":"RUN_FINISHED"' in frame for frame in frames))

    async def test_application_lifecycle_get_stays_fail_open_when_scanner_storage_fails(self) -> None:
        """scanner 底层故障不应把独立 lifecycle get 变成失败响应。"""

        ensure_application_lifecycle(
            self.workspace,
            application_id="app-001",
            application_name="测试应用",
        )
        with patch(
            "app.services.execution_recovery_scanner.reconcile_orphaned_executions",
            AsyncMock(side_effect=OSError("database unavailable")),
        ):
            frames = [
                frame
                async for frame in build_application_lifecycle_ag_ui_stream(
                    payload={
                        "threadId": "thread-lifecycle-fail-open",
                        "runId": "run-lifecycle-fail-open",
                        "forwardedProps": {
                            "applicationLifecycle": {
                                "action": "get",
                                "workspaceRoot": str(self.workspace),
                            }
                        },
                    }
                )
            ]

        self.assertTrue(any('"type":"RUN_FINISHED"' in frame for frame in frames))
        self.assertFalse(any('"type":"RUN_ERROR"' in frame for frame in frames))

    async def test_runtime_pre_scans_old_orphan_before_new_execution(self) -> None:
        """新 Workflow 创建 Execution 前必须先处理中断的旧 owner。"""

        await self._insert_running(
            "run-old-before-new",
            owner_backend_instance_id="backend-old",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        graph = _CountingGraph()
        with patch(
            "app.protocols.workflow.runtime.current_backend_instance",
            return_value=SimpleNamespace(instance_id="backend-new", pid=456),
        ):
            frames = [
                frame
                async for frame in build_workflow_ag_ui_stream(
                    graph=graph,
                    payload=self._workflow_payload(
                        thread_id="thread-new",
                        run_id="run-new",
                    ),
                )
            ]

        old = await get_execution(self.workspace, "run-old-before-new")
        new = await get_execution(self.workspace, "run-new")
        self.assertFalse(any('"type":"RUN_ERROR"' in frame for frame in frames))
        self.assertIsNotNone(old)
        self.assertIsNotNone(new)
        assert old is not None
        assert new is not None
        self.assertEqual(old.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(new.status, DurableExecutionStatus.COMPLETED)

    async def test_current_active_run_is_not_killed_by_new_workflow_scan(self) -> None:
        """同工作区启动并行新 Workflow 时，当前活跃旧 run 必须保持 RUNNING。"""

        now = datetime.now(timezone.utc)
        await self._insert_running(
            "run-active-before-new",
            owner_backend_instance_id="backend-current",
            expires_at=now - timedelta(seconds=1),
        )
        current_task = asyncio.current_task()
        self.assertIsNotNone(current_task)
        assert current_task is not None
        workflow_run_registry.register(
            "run-active-before-new",
            current_task,
            workspace=str(self.workspace),
        )
        try:
            with patch(
                "app.protocols.workflow.runtime.current_backend_instance",
                return_value=SimpleNamespace(instance_id="backend-current", pid=456),
            ):
                frames = [
                    frame
                    async for frame in build_workflow_ag_ui_stream(
                        graph=_CountingGraph(),
                        payload=self._workflow_payload(
                            thread_id="thread-parallel",
                            run_id="run-parallel",
                        ),
                    )
                ]
        finally:
            workflow_run_registry.unregister("run-active-before-new", current_task)

        active = await get_execution(self.workspace, "run-active-before-new")
        active_lease = await get_execution_lease(self.workspace, "run-active-before-new")
        self.assertFalse(any('"type":"RUN_ERROR"' in frame for frame in frames))
        self.assertIsNotNone(active)
        self.assertIsNotNone(active_lease)
        assert active is not None
        assert active_lease is not None
        self.assertEqual(active.status, DurableExecutionStatus.RUNNING)
        self.assertEqual(active_lease.status, ExecutionLeaseStatus.ACTIVE)
        self.assertGreater(active_lease.expires_at, now)

    async def test_runtime_run_id_conflict_is_fail_closed_without_graph_or_lifecycle_side_effects(self) -> None:
        """Runtime 冲突必须输出 RUN_ERROR，且不启动 Graph、heartbeat、现场或 lifecycle 失败。"""

        await observe_execution_started(
            workspace=str(self.workspace),
            project_id=None,
            thread_id="thread-old",
            run_id="run-runtime-conflict",
            workflow_scope="page",
            first_node="A",
            backend_instance_id="backend-old",
            backend_pid=123,
        )
        await mark_execution_interrupted(
            workspace=self.workspace,
            run_id="run-runtime-conflict",
            interrupted_at=datetime.now(timezone.utc),
        )
        graph = _CountingGraph()
        with patch("app.protocols.workflow.runtime.begin_workflow_lifecycle") as begin:
            frames = [
                frame
                async for frame in build_workflow_ag_ui_stream(
                    graph=graph,
                    payload=self._workflow_payload(
                        thread_id="thread-new",
                        run_id="run-runtime-conflict",
                    ),
                )
            ]

        old = await get_execution(self.workspace, "run-runtime-conflict")
        points = await list_recovery_points(self.workspace, "run-runtime-conflict")
        payload = "".join(frames)
        self.assertIn("DURABLE_EXECUTION_RUN_ID_CONFLICT", payload)
        self.assertIn('"type":"RUN_ERROR"', payload)
        self.assertNotIn('"type":"RUN_FINISHED"', payload)
        self.assertFalse(graph.astream_started)
        begin.assert_not_called()
        self.assertIsNotNone(old)
        assert old is not None
        self.assertEqual(old.status, DurableExecutionStatus.INTERRUPTED)
        self.assertEqual(points, [])

    async def test_runtime_active_registry_conflict_preserves_old_owner(self) -> None:
        """进程内 active 冲突必须阻止新请求且保留旧 task 的取消 owner。"""

        old_started = asyncio.Event()
        old_release = asyncio.Event()

        async def old_workflow() -> None:
            """保持旧 Workflow 活跃，模拟 Graph 正在执行。"""

            old_started.set()
            await old_release.wait()

        old_task = asyncio.create_task(old_workflow())
        workflow_run_registry.register(
            "run-active-registry-conflict",
            old_task,
            workspace=str(self.workspace),
        )
        await old_started.wait()
        graph = _CountingGraph()
        try:
            with (
                patch("app.protocols.workflow.runtime.begin_workflow_lifecycle") as begin,
                patch("app.protocols.workflow.runtime.workspace_run_leases.acquire") as acquire,
                patch("app.protocols.workflow.run_control.workspace_process_registry.allow_run") as allow_run,
            ):
                frames = [
                    frame
                    async for frame in build_workflow_ag_ui_stream(
                        graph=graph,
                        payload=self._workflow_payload(
                            thread_id="thread-duplicate",
                            run_id="run-active-registry-conflict",
                        ),
                    )
                ]

            payload = "".join(frames)
            self.assertIn("WORKFLOW_RUN_ALREADY_ACTIVE", payload)
            self.assertIn('"type":"RUN_ERROR"', payload)
            self.assertFalse(graph.astream_started)
            self.assertTrue(workflow_run_registry.is_active(
                "run-active-registry-conflict",
                workspace=str(self.workspace),
            ))
            begin.assert_not_called()
            acquire.assert_not_called()
            allow_run.assert_not_called()
            self.assertEqual(
                await workflow_run_registry.cancel_and_wait(
                    "run-active-registry-conflict",
                    workspace=str(self.workspace),
                ),
                "cancelled",
            )
        finally:
            old_release.set()
            await asyncio.gather(old_task, return_exceptions=True)
            workflow_run_registry.unregister(
                "run-active-registry-conflict",
                old_task,
            )

    async def test_recovery_start_storage_failure_stays_fail_open_without_fake_lease(self) -> None:
        """Execution start 普通写库故障仍允许 Graph 完成，但不产生伪造 lease/现场。"""

        graph = _CountingGraph()
        with patch(
            "app.protocols.workflow.runtime.observe_execution_started",
            AsyncMock(side_effect=OSError("database unavailable")),
        ):
            frames = [
                frame
                async for frame in build_workflow_ag_ui_stream(
                    graph=graph,
                    payload=self._workflow_payload(
                        thread_id="thread-fail-open",
                        run_id="run-fail-open",
                    ),
                )
            ]

        self.assertTrue(graph.astream_started)
        self.assertIn('"type":"RUN_FINISHED"', "".join(frames))
        self.assertIsNone(await get_execution(self.workspace, "run-fail-open"))
        self.assertIsNone(await get_execution_lease(self.workspace, "run-fail-open"))
        self.assertEqual(await list_recovery_points(self.workspace, "run-fail-open"), [])

    async def test_best_effort_observation_rethrows_run_id_conflict(self) -> None:
        """通用旁路 helper 只能吞恢复故障，不能吞 runId 身份冲突。"""

        from app.services.execution_recovery import best_effort_recovery_observation

        callback = AsyncMock(
            side_effect=DurableExecutionRunConflictError(
                run_id="run-conflict",
                existing_status="interrupted",
                existing_thread_id="thread-old",
            )
        )
        with self.assertRaises(DurableExecutionRunConflictError):
            await best_effort_recovery_observation(
                operation="execution.started",
                workspace=str(self.workspace),
                run_id="run-conflict",
                thread_id="thread-new",
                workflow_scope="page",
                callback=callback,
            )
        callback.assert_awaited_once()

    async def _insert_running(
        self,
        run_id: str,
        *,
        owner_backend_instance_id: str,
        expires_at: datetime,
    ) -> None:
        """按给定 owner 和过期时间写入测试用 RUNNING + ACTIVE 组合。"""

        now = datetime.now(timezone.utc)
        record = self._record(run_id, thread_id=f"thread-{run_id}", now=now)
        lease = ExecutionLease(
            run_id=run_id,
            owner_backend_instance_id=owner_backend_instance_id,
            owner_pid=123,
            status=ExecutionLeaseStatus.ACTIVE,
            acquired_at=now,
            heartbeat_at=now,
            expires_at=expires_at,
        )
        await insert_execution_with_lease(record=record, lease=lease)

    async def _collect_workflow(
        self,
        graph: object,
        *,
        thread_id: str,
        run_id: str,
    ) -> list[str]:
        """消费测试 Workflow 流，直到调用方通过 registry 取消。"""

        return [
            frame
            async for frame in build_workflow_ag_ui_stream(
                graph=graph,
                payload=self._workflow_payload(thread_id=thread_id, run_id=run_id),
            )
        ]

    def _record(
        self,
        run_id: str,
        *,
        thread_id: str,
        now: datetime | None = None,
    ) -> DurableExecutionRecord:
        """构造恢复库测试使用的最小 ExecutionRecord。"""

        timestamp = now or datetime.now(timezone.utc)
        return DurableExecutionRecord(
            run_id=run_id,
            thread_id=thread_id,
            workspace=str(self.workspace),
            project_id=None,
            execution_kind="workbench",
            workflow_scope="page",
            first_node="A",
            current_node="A",
            status=DurableExecutionStatus.RUNNING,
            started_at=timestamp,
            updated_at=timestamp,
        )

    def _point(self, run_id: str, point_id: str) -> RecoveryPoint:
        """构造可用于 legacy scanner 断言的 ENTRY RecoveryPoint。"""

        return RecoveryPoint(
            recovery_point_id=point_id,
            run_id=run_id,
            thread_id=f"thread-{run_id}",
            kind=RecoveryPointKind.ENTRY,
            graph_node="A",
            next_nodes=["A"],
            captured_at=datetime.now(timezone.utc),
        )

    def _workflow_payload(self, *, thread_id: str, run_id: str) -> dict[str, object]:
        """构造不进入 application planning 的最小主 Workflow 请求。"""

        return {
            "threadId": thread_id,
            "runId": run_id,
            "message": "执行 recovery lease 测试",
            "forwardedProps": {"workspaceRoot": str(self.workspace)},
        }


__all__ = ["ExecutionRecoveryLeaseTests"]
