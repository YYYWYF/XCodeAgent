"""验证生成诊断穿透修复输入与暂停超时后的真实退出收口。"""

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path

from app.graph.subgraphs.testing import validate_generated_unit_tests
from app.services.test_validation import create_revision_requests
from app.services.application_lifecycle import (
    load_application_lifecycle, start_workbench_execution, handoff_workbench_execution_for_recovery,
)
from app.protocols.workflow.run_control import WorkflowRunRegistry
from app.services.run_workers import bind_run_workers, run_worker
from app.services.stopped_run_cleanup import _pending
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class GenerationDiagnosticsTests(unittest.TestCase):
    """实际内容校验失败必须同时到达进度、质量报告与修复请求。"""

    def test_generation_exception_without_validation_keeps_original_reason(self):
        """没有文件违规时保留真实工具错误，不能被通用诊断文案覆盖。"""
        result = validate_generated_unit_tests({
            'unit_test_generation_context': {'affected_layers': ['frontend']},
            'unit_test_generation': {'status': 'failed', 'summary': '工具返回文件读取失败：permission denied'},
        }, {})
        self.assertIn('permission denied', result['test_results'][0]['evidence'])

    def test_forbidden_assertion_reaches_repair_with_path_line_and_rule(self):
        """模型的成功总结不能覆盖确定性违规，修复输入保留完整明细。"""
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            test = root / 'frontend/tests/Journey.test.tsx'
            test.parent.mkdir(parents=True)
            test.write_text("test('journey', () => {\nexpect(title).toHaveStyle({fontSize: '28px'});\n});\n")
            result = validate_generated_unit_tests({
                'workspace': workspace,
                'unit_test_generation_context': {'affected_layers': ['frontend']},
                'unit_test_generation': {'status': 'completed', 'test_files': ['frontend/tests/Journey.test.tsx'],
                                         'summary': 'All tests are already covered.', 'validation': {}},
            }, {})
            check = result['test_results'][0]
            self.assertFalse(check['passed'])
            self.assertIn('toHaveStyle（第 2 行）', check['evidence'])
            self.assertIn('frontend/tests/Journey.test.tsx', check['evidence'])
            self.assertIn('不要改生产代码', check['evidence'])
            request = create_revision_requests([check], source='unit_test')[0]
            self.assertIn('toHaveStyle', request['failed_attempt']['agent_note'])
            self.assertEqual(request['failed_check']['generation_failure_details'][0]['violations'],
                             [{'token': 'toHaveStyle', 'line': 2}])


class StopCleanupTests(unittest.IsolatedAsyncioTestCase):
    """用真实后台线程证明超时后自动清锁，且恢复重新申请资源。"""

    async def test_pause_timeout_eventually_releases_only_own_resources(self):
        """不提前放行写入，不要求用户重试暂停，不清除其他任务的锁。"""
        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            for run, page in (('paused', 'page1'), ('survivor', 'page2')):
                start_workbench_execution(workspace, scope='page', target_id=page, page_id=page,
                                          thread_id=run, owner_session_id=run, run_id=run, phase='unit_test')
            started, release = threading.Event(), threading.Event()

            def work():
                """保留一次在途同步工作直到测试允许真实退出。"""
                with bind_run_workers('paused', workspace), run_worker():
                    started.set()
                    release.wait(3)

            async def outer():
                """取消 asyncio 不会终止正在运行的线程。"""
                await asyncio.to_thread(work)

            registry = WorkflowRunRegistry()
            task = asyncio.create_task(outer())
            registry.register('paused', task, workspace=workspace)
            await asyncio.to_thread(started.wait, 2)
            try:
                self.assertEqual(await registry.cancel_and_wait('paused', workspace=workspace, timeout_seconds=.01),
                                 'cancel_timeout')
                self.assertIn('page1', load_application_lifecycle(workspace).resource_locks.pages)
                cleanup = _pending[(str(Path(workspace).resolve()), 'paused')]
                release.set()
                await asyncio.wait_for(asyncio.shield(cleanup), 3)
                current = load_application_lifecycle(workspace)
                self.assertEqual(current.active_executions['paused'].status, 'stopped')
                self.assertNotIn('page1', current.resource_locks.pages)
                self.assertEqual(current.resource_locks.pages['page2'].run_id, 'survivor')
                resumed = handoff_workbench_execution_for_recovery(
                    workspace, source_run_id='paused', new_run_id='continued', thread_id='paused',
                    phase='unit_test', expected_lifecycle_revision=current.revision,
                )
                self.assertEqual(resumed.resource_locks.pages['page1'].run_id, 'continued')
                self.assertEqual(resumed.resource_locks.pages['page2'].run_id, 'survivor')
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    async def test_repeated_stop_releases_previously_stopped_idle_execution(self):
        """后端重启后读取现有暂停执行，显式停止仍能清理旧锁，不依赖进程内监听器。"""
        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            start_workbench_execution(workspace, scope='application', target_id='application', page_id=None,
                                      thread_id='thread', owner_session_id='session', run_id='idle', phase='unit_test')
            self.assertEqual(await WorkflowRunRegistry().cancel_and_wait('idle', workspace=workspace), 'not_running')
            current = load_application_lifecycle(workspace)
            self.assertIsNone(current.resource_locks.application)
            self.assertEqual(current.active_executions['idle'].status, 'stopped')
