"""四类重试断点的隔离回归：交接、入场、最终释放与 checkpoint 等待。"""

import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests import test_retry_preparation_failures as fixtures
from app.services.checkpoint_first_retry import prepare_checkpoint_first_retry
from app.services.application_lifecycle import load_application_lifecycle, end_workbench_execution
from app.services.execution_cleanup import (
    best_effort_checkpoint_housekeeping, finalize_runtime_ownership, retry_cleanup_persistence,
)
from app.services.retry_preparation import await_checkpoint_preparation, pending_checkpoint_preparations
from app.services.failed_run_cleanup import wait_failed_run
from app.workspace.run_lease import workspace_run_leases
from app.persistence.execution_recovery import get_execution


class RetryBoundaryFailureTests(unittest.IsolatedAsyncioTestCase):
    """使用真实交接、checkpoint 和租约，故障仅注入平台准备边界。"""

    async def assert_original_retry_works(self, root, source, graph):
        """交接失败后原入口仍可执行，并保持原始提问。"""
        context = await prepare_checkpoint_first_retry(source, graph)
        try:
            chunks = [item async for item in context.graph.astream(context.fork_snapshot.values,
                config=context.fork_config, stream_mode=["updates", "custom"])]
            self.assertTrue(chunks)
            self.assertEqual(context.fork_snapshot.values["request"], "原始问题 ORIGINAL")
        finally:
            context.workspace_lease.release()
            end_workbench_execution(root, run_id=context.new_run_id, missing_ok=True)

    async def test_failed_acquire_or_registration_restores_source(self):
        """获取租约或登记新 Run 失败后，原失败记录和入口均保留。"""
        for location in ("app.services.execution_recovery_executor._acquire_workspace_lease",
                         "app.services.execution_recovery.observe_execution_started"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as root:
                source, graph, _ = await fixtures.RetryPreparationFailureTests().fixture(root)
                with patch(location, side_effect=RuntimeError("temporary preparation failure")):
                    with self.assertRaisesRegex(RuntimeError, "重试准备未完成"):
                        await prepare_checkpoint_first_retry(source, graph)
                state = load_application_lifecycle(root)
                self.assertEqual(set(state.active_executions), {source.run_id})
                self.assertEqual(state.active_executions[source.run_id].status.value, "failed")
                self.assertFalse(any(state.resource_locks.model_dump().values()))
                self.assertIsNone(workspace_run_leases.active_owner(workspace_root=root, project_id=None))
                await self.assert_original_retry_works(root, source, graph)

    async def test_abandoned_planning_child_is_not_a_new_recovery_candidate(self):
        """规划准备撤销后恢复原 activeRun，新 child 不得抢占原失败卡的恢复入口。"""
        from app.services import application_lifecycle as lifecycle_service
        from app.services import execution_recovery as recovery_service
        from app.persistence.execution_recovery import list_recovery_projection_candidates
        with tempfile.TemporaryDirectory() as root:
            source, graph, _ = await fixtures.RetryPreparationFailureTests().fixture(root)
            source = source.model_copy(update={"execution_kind": "application_planning", "workflow_scope": "application_planning"})
            current = load_application_lifecycle(root)
            lifecycle_service.write_application_lifecycle(root, current.model_copy(update={
                "active_run_id": source.run_id, "active_executions": {},
                "resource_locks": lifecycle_service._resource_locks_without_run(current.resource_locks, source.run_id),
                "initialization": current.initialization.model_copy(update={"thread_id": source.thread_id}),
            }))
            register = recovery_service.observe_execution_started
            child_ids = []

            async def register_then_fail(**kwargs):
                """模拟登记已经写入后才返回存储异常，触发完整撤销。"""
                child = await register(**kwargs)
                child_ids.append(child.run_id)
                raise OSError("registration acknowledgement failed")

            with patch("app.services.execution_recovery.observe_execution_started", register_then_fail):
                with self.assertRaisesRegex(RuntimeError, "重试准备未完成"):
                    await prepare_checkpoint_first_retry(source, graph)
            self.assertEqual(load_application_lifecycle(root).active_run_id, source.run_id)
            self.assertEqual((await get_execution(root, child_ids[0])).status.value, "cancelled")
            candidates = await list_recovery_projection_candidates(root)
            self.assertNotIn(child_ids[0], {record.run_id for record in candidates})

    async def test_handled_protocol_failure_before_runtime_restores_source(self):
        """AG-UI 自行收口的入场失败同样回退，不留下 running child 和租约。"""
        from app.protocols.execution_recovery import _native_recovery_stream
        with tempfile.TemporaryDirectory() as root:
            source, graph, _ = await fixtures.RetryPreparationFailureTests().fixture(root)
            source = source.model_copy(update={"workflow_scope": "conversation"})
            context = await prepare_checkpoint_first_retry(source, graph)
            with patch("app.protocols.direct_modification.validate_selected_user_skills",
                       side_effect=RuntimeError("before runtime")):
                frames = "".join([item async for item in _native_recovery_stream(context, None)])
            self.assertIn("before runtime", frames)
            self.assertFalse(context.runtime_started)
            self.assertEqual(set(load_application_lifecycle(root).active_executions), {source.run_id})
            self.assertIsNone(workspace_run_leases.active_owner(workspace_root=root, project_id=None))
            self.assertEqual((await get_execution(root, context.new_run_id)).status.value, "cancelled")
            await self.assert_original_retry_works(root, source, graph)

    async def test_housekeeping_failure_and_timeout_do_not_reject_request(self):
        """例行清理报错或挂起都在期限内返回，让业务继续且不堆积维护。"""
        cancelled = asyncio.Event()

        async def hang(**kwargs):
            """模拟等候数据库的维护任务，接到取消后退出。"""
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch("app.persistence.checkpoints.cleanup_workflow_checkpoints",
                   AsyncMock(side_effect=RuntimeError("temporary housekeeping failure"))):
            await best_effort_checkpoint_housekeeping("isolated-housekeeping")
        await asyncio.sleep(0)
        with patch("app.persistence.checkpoints.cleanup_workflow_checkpoints", hang), \
             patch("app.services.execution_cleanup.HOUSEKEEPING_SECONDS", .02):
            await asyncio.wait_for(best_effort_checkpoint_housekeeping("isolated-housekeeping"), .5)
            await asyncio.wait_for(cancelled.wait(), .5)

    async def test_failed_persistence_retries_without_skipping_final_release(self):
        """写盘失败有后台责任，心跳、登记、租约和规划锁仍各自释放。"""
        calls, persisted = [], asyncio.Event()
        lock = asyncio.Lock()
        await lock.acquire()

        async def persist():
            """第一次失败，随后存储恢复并补齐原失败终态。"""
            calls.append("persist")
            if calls.count("persist") == 1:
                raise OSError("temporary disk error")
            persisted.set()

        await retry_cleanup_persistence(persist)
        lease = SimpleNamespace(release=lambda: calls.append("lease"))
        with patch("app.services.execution_lease_heartbeat.stop_execution_heartbeat",
                   AsyncMock(side_effect=RuntimeError("heartbeat cleanup error"))):
            with self.assertRaisesRegex(RuntimeError, "heartbeat cleanup error"):
                await finalize_runtime_ownership(heartbeat=None, unregister=lambda: calls.append("unregister"),
                    lease=lease, planning_lock=lock)
        self.assertFalse(lock.locked())
        self.assertIn("unregister", calls)
        self.assertIn("lease", calls)
        await asyncio.wait_for(persisted.wait(), 1)

    async def test_slow_persistence_does_not_hold_final_release(self):
        """状态存储一直等待时也先完成退出登记，原补写完成前不重复提交。"""
        release, persisted = asyncio.Event(), asyncio.Event()

        async def persist():
            """保持原存储操作直到允许完成，不伪造成功。"""
            await release.wait()
            persisted.set()

        try:
            with patch("app.services.execution_cleanup.CLEANUP_WRITE_SECONDS", .02):
                await asyncio.wait_for(retry_cleanup_persistence(persist), .5)
            self.assertFalse(persisted.is_set())
        finally:
            release.set()
            await asyncio.wait_for(persisted.wait(), 1)

    async def test_checkpoint_timeout_preserves_source_and_next_retry_succeeds(self):
        """暂时读超时不等于缺失，原入口保留且存储恢复后同一请求可以继续。"""
        from app.domain.execution_recovery import RecoveryExecutionError
        with tempfile.TemporaryDirectory() as root:
            source, graph, _ = await fixtures.RetryPreparationFailureTests().fixture(root)

            async def hang(config):
                """模拟 checkpoint 当前现场读取迟迟没有结果。"""
                await asyncio.Event().wait()

            with patch.object(graph, "aget_state", hang), \
                 patch("app.services.retry_preparation.CHECKPOINT_PREPARATION_SECONDS", .02):
                with self.assertRaises(RecoveryExecutionError) as missing:
                    await prepare_checkpoint_first_retry(source, graph)
                self.assertEqual(missing.exception.code, "RETRY_CHECKPOINT_UNAVAILABLE")
            self.assertIn(source.run_id, load_application_lifecycle(root).active_executions)
            context = await prepare_checkpoint_first_retry(source, graph)
            try:
                chunks = [chunk async for chunk in context.graph.astream(context.fork_snapshot.values,
                    config=context.fork_config, stream_mode=["updates", "custom"])]
                self.assertTrue(chunks)
            finally:
                context.workspace_lease.release()

    async def test_rollback_preserves_another_execution_created_after_handoff(self):
        """只回退本次交接，不覆盖交接后其他会话新建的执行与资源。"""
        from app.services.retry_preparation import abandon_prepared_retry
        from app.services.application_lifecycle import start_workbench_execution
        with tempfile.TemporaryDirectory() as root:
            source, graph, _ = await fixtures.RetryPreparationFailureTests().fixture(root)
            context = await prepare_checkpoint_first_retry(source, graph)
            start_workbench_execution(root, scope="page", target_id="page-b", page_id="page-b",
                thread_id="other-thread", run_id="other-run", phase="build", owner_session_id="other-session")
            await abandon_prepared_retry(workspace=root, source=source,
                before=context.retry_lifecycle_before_handoff, new_run_id=context.new_run_id,
                lease=context.workspace_lease)
            state = load_application_lifecycle(root)
            self.assertEqual(set(state.active_executions), {source.run_id, "other-run"})
            self.assertEqual(state.resource_locks.pages["page-b"].run_id, "other-run")
            self.assertEqual(state.active_executions[source.run_id].status.value, "failed")

    async def test_checkpoint_update_timeout_falls_back_once(self):
        """恢复准备没有返回时取消一次，仍在同次重试执行业务节点。"""
        with tempfile.TemporaryDirectory() as root:
            source, graph, received = await fixtures.RetryPreparationFailureTests().fixture(root)
            context = await prepare_checkpoint_first_retry(source, graph)
            cancelled = asyncio.Event()

            async def hang(*args, **kwargs):
                """只挂起 checkpoint 更新，不替换真实业务节点。"""
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            try:
                with patch.object(graph, "aupdate_state", hang), \
                     patch("app.services.retry_preparation.CHECKPOINT_PREPARATION_SECONDS", .02):
                    chunks = [item async for item in context.graph.astream(context.fork_snapshot.values,
                        config=context.fork_config, stream_mode=["updates", "custom"])]
                self.assertTrue(chunks)
                self.assertTrue(cancelled.is_set())
                self.assertEqual(len(received), 2)
                self.assertEqual(received[-1]["request"], "原始问题 ORIGINAL")
            finally:
                context.workspace_lease.release()
                end_workbench_execution(root, run_id=context.new_run_id, missing_ok=True)

    async def test_late_preparation_cannot_overlap_business_retry(self):
        """不响应取消的准备操作仍算活跃工作，超时不等于已经退出。"""
        release = asyncio.Event()

        async def stubborn():
            """模拟取消后仍在完成存储提交的异步准备任务。"""
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()

        try:
            with patch("app.services.retry_preparation.CHECKPOINT_PREPARATION_SECONDS", .02):
                with self.assertRaises(TimeoutError):
                    await await_checkpoint_preparation(stubborn(), "late-prepare")
            self.assertTrue(pending_checkpoint_preparations("late-prepare"))
            with patch("app.services.failed_run_cleanup.FOREGROUND_CLEANUP_SECONDS", .02):
                self.assertFalse(await wait_failed_run("late-prepare"))
        finally:
            release.set()
            await asyncio.wait_for(wait_failed_run("late-prepare"), 1)
        self.assertFalse(pending_checkpoint_preparations("late-prepare"))
