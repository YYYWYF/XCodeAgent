"""统一重试读取失败保留原入口，不引入辅助恢复记录。"""
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from app.services.application_lifecycle import load_application_lifecycle, start_workbench_execution
from app.services.business_node_retry import prepare_business_node_retry
from app.domain.execution_recovery import RecoveryExecutionError
from app.protocols.workflow.lifecycle import fail_workflow_lifecycle
from app.workspace.run_lease import workspace_run_leases
from tests.test_business_node_retry import BusinessNodeRetryTests
from tests.test_checkpoint_first_retry import CheckpointFirstRetryTests
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class RetryPreparationFailureTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 checkpoint 与生命周期，故障仅注入准备边界。"""

    async def fixture(self, root):
        """构造有完整上下文及原问题的真实失败运行。"""
        def behavior(count, state):
            """首次失败，下一次执行成功。"""
            if count == 1:
                raise RuntimeError('original failure')
            return {'status': 'completed'}
        _write_ready_workbench_lifecycle(root)
        start_workbench_execution(root, scope='page', target_id='page-a', page_id='page-a',
            thread_id='thread', run_id='source', phase='build', owner_session_id='session')
        fail_workflow_lifecycle(root, run_id='source', phase='build', error=RuntimeError('original failure'))
        graph, _, _, _, _, received = await CheckpointFirstRetryTests().fixture(root, behavior)
        return BusinessNodeRetryTests().source(root), graph, received

    async def test_checkpoint_read_failure_precedes_handoff(self):
        """读取失败不交接、不创建新租约，存储恢复后原任务仍可重试。"""
        with tempfile.TemporaryDirectory() as root:
            source, graph, received = await self.fixture(root)
            with patch.object(graph, 'aget_state', AsyncMock(side_effect=OSError('temporary read failure'))):
                with self.assertRaises(RecoveryExecutionError) as failure:
                    await prepare_business_node_retry(source, graph)
                self.assertEqual(failure.exception.code, 'RETRY_CHECKPOINT_UNAVAILABLE')
            self.assertEqual(set(load_application_lifecycle(root).active_executions), {'source'})
            self.assertIsNone(workspace_run_leases.active_owner(workspace_root=root, project_id=None))
            context = await prepare_business_node_retry(source, graph)
            try:
                await graph.ainvoke(context.fork_snapshot.values, context.fork_config)
                self.assertEqual(received[-1]['request'], '原始问题 ORIGINAL')
            finally:
                context.workspace_lease.release()

    async def test_missing_checkpoint_releases_source(self):
        """读取成功但确无现场时提示重新发起，释放原失败占用。"""
        with tempfile.TemporaryDirectory() as root:
            source, graph, _ = await self.fixture(root)
            async def empty_history(config):
                """返回读取成功的空历史。"""
                if False:
                    yield None
            with patch.object(graph, 'aget_state_history', empty_history), patch.object(graph, 'aget_state', AsyncMock(return_value=None)):
                with self.assertRaises(RecoveryExecutionError) as missing:
                    await prepare_business_node_retry(source, graph)
                self.assertEqual(missing.exception.code, 'RETRY_ENTRY_NOT_FOUND')
            self.assertNotIn('source', load_application_lifecycle(root).active_executions)
