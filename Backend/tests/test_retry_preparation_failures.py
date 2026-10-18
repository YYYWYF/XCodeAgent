"""重试元数据异常不能阻断 checkpoint 定位或留下未启动的新执行占用。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.application_lifecycle import load_application_lifecycle, start_workbench_execution
from app.services.business_node_retry import read_retry_hint, retry_action_plan
from app.services.checkpoint_first_retry import prepare_checkpoint_first_retry
from app.services.retry_origin import RetryOrigin, _path, load_retry_origin, save_retry_origin
from app.domain.execution_recovery import RecoveryExecutionError
from app.protocols.workflow.lifecycle import fail_workflow_lifecycle
from app.workspace.run_lease import workspace_run_leases
from tests.test_business_node_retry import BusinessNodeRetryTests
from tests.test_checkpoint_first_retry import CheckpointFirstRetryTests
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class RetryPreparationFailureTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 checkpoint 和 Lifecycle 交接验证准备失败与下一次恢复。"""

    async def fixture(self, root):
        """构造有原问题和失败节点的真实运行，不调用模型。"""
        def behavior(count, state):
            """原执行失败，恢复或节点重新执行时完成。"""
            if count == 1:
                raise RuntimeError("original failure")
            return {"status": "completed"}

        _write_ready_workbench_lifecycle(root)
        start_workbench_execution(root, scope="page", target_id="page-a", page_id="page-a",
            thread_id="thread", run_id="source", phase="build", owner_session_id="session")
        fail_workflow_lifecycle(root, run_id="source", phase="build", error=RuntimeError("original failure"))
        graph, _, _, _, _, received = await CheckpointFirstRetryTests().fixture(root, behavior)
        return BusinessNodeRetryTests().source(root), graph, received

    async def test_bad_origin_still_finds_checkpoint_node_and_question(self):
        """损坏、非法字段及其他 Thread 记录都不能遮住本 Run 的真实 checkpoint。"""
        invalid_records = ["{", '{"unknown":true}', RetryOrigin(thread_id="other-thread",
            source_run_id="source", node="wrong-node", request="wrong-question").model_dump_json()]
        for invalid in invalid_records:
            with self.subTest(record=invalid), tempfile.TemporaryDirectory() as root:
                source, graph, received = await self.fixture(root)
                path = _path(root, "source")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(invalid)
                hint = await read_retry_hint(source, graph)
                self.assertEqual((hint.node, hint.request), ("build", "原始问题 ORIGINAL"))
                self.assertIsNotNone((await retry_action_plan(source, graph)).primary_action)
                context = await prepare_checkpoint_first_retry(source, graph)
                try:
                    async for _ in context.graph.astream(context.fork_snapshot.values,
                            config=context.fork_config, stream_mode=["updates", "custom"]):
                        pass
                    self.assertEqual(received[-1]["request"], "原始问题 ORIGINAL")
                    self.assertEqual(len(received), 2)
                finally:
                    context.workspace_lease.release()

    async def test_origin_read_io_failure_is_optional(self):
        """辅助文件读失败后仍能用 checkpoint 生成重试入口。"""
        with tempfile.TemporaryDirectory() as root:
            source, graph, _ = await self.fixture(root)
            save_retry_origin(root, "source", RetryOrigin(thread_id="thread", source_run_id="source",
                node="build", request="原始问题 ORIGINAL"))
            original_read = Path.read_text

            def read(path, *args, **kwargs):
                """只模拟指定辅助记录的临时读取故障。"""
                if path == _path(root, "source"):
                    raise OSError("temporary origin read failure")
                return original_read(path, *args, **kwargs)

            with patch.object(Path, "read_text", read):
                self.assertIsNone(load_retry_origin(root, "source", "thread"))
                self.assertEqual((await read_retry_hint(source, graph)).request, "原始问题 ORIGINAL")

    async def test_bad_origin_and_failed_restore_fall_back_in_one_retry(self):
        """损坏记录不阻断恢复，checkpoint 恢复失败后同次点击重执行原节点与原问题。"""
        with tempfile.TemporaryDirectory() as root:
            source, graph, received = await self.fixture(root)
            path = _path(root, "source")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{")
            context = await prepare_checkpoint_first_retry(source, graph)
            try:
                with patch.object(graph, "aupdate_state", AsyncMock(side_effect=ValueError("restore failed"))) as restore:
                    async for _ in context.graph.astream(context.fork_snapshot.values,
                            config=context.fork_config, stream_mode=["updates", "custom"]):
                        pass
                    restore.assert_awaited_once()
                self.assertEqual(len(received), 2)
                self.assertEqual(received[-1]["request"], "原始问题 ORIGINAL")
                self.assertNotIn("clarification", received[-1])
            finally:
                context.workspace_lease.release()

    async def test_bad_origin_without_checkpoint_releases_source(self):
        """辅助记录和 checkpoint 都不可用时释放旧占用并返回现有可关闭提示。"""
        with tempfile.TemporaryDirectory() as root:
            source, graph, _ = await self.fixture(root)
            path = _path(root, "source")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{")

            async def empty_history(config):
                """模拟本 Run 没有成功提交任何 checkpoint。"""
                if False:
                    yield None

            with patch.object(graph, "aget_state_history", empty_history), \
                 patch.object(graph, "aget_state", AsyncMock(return_value=None)):
                with self.assertRaises(RecoveryExecutionError) as missing:
                    await prepare_checkpoint_first_retry(source, graph)
                self.assertEqual(missing.exception.code, "RETRY_ENTRY_NOT_FOUND")
            self.assertNotIn("source", load_application_lifecycle(root).active_executions)
            self.assertIsNone(workspace_run_leases.active_owner(workspace_root=root, project_id=None))

    async def test_origin_write_failure_precedes_handoff_and_next_retry_succeeds(self):
        """任一锚点写入失败都不创建新 owner 或租约，下一次仍从原入口执行。"""
        for failure_at in (1, 2):
            with self.subTest(write=failure_at), tempfile.TemporaryDirectory() as root:
                source, graph, received = await self.fixture(root)
                calls = []

                def save(workspace, run_id, origin):
                    """指定一次落盘失败，其余写入沿用真实原子存储。"""
                    calls.append(run_id)
                    if len(calls) == failure_at:
                        raise OSError("temporary origin write failure")
                    save_retry_origin(workspace, run_id, origin)

                with patch("app.services.retry_origin.save_retry_origin", side_effect=save), \
                        patch("app.services.application_lifecycle.handoff_workbench_execution_for_recovery") as handoff, \
                        patch("app.services.execution_recovery_executor._acquire_workspace_lease") as acquire:
                    with self.assertRaisesRegex(OSError, "temporary origin write failure"):
                        await prepare_checkpoint_first_retry(source, graph)
                    handoff.assert_not_called()
                    acquire.assert_not_called()
                lifecycle = load_application_lifecycle(root)
                self.assertEqual(set(lifecycle.active_executions), {"source"})
                self.assertIsNone(workspace_run_leases.active_owner(workspace_root=root, project_id=None))
                context = await prepare_checkpoint_first_retry(source, graph)
                try:
                    async for _ in context.graph.astream(context.fork_snapshot.values,
                            config=context.fork_config, stream_mode=["updates", "custom"]):
                        pass
                    self.assertEqual(received[-1]["request"], "原始问题 ORIGINAL")
                finally:
                    context.workspace_lease.release()
