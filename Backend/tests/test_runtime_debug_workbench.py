"""调试启动的依赖准备与拥有者边界，不启动进程或调用模型。"""
from contextlib import nullcontext
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from app.services import agent_runtime_workbench as service


class RuntimeDebugWorkbenchTests(TestCase):
    """验证首次调试安装依赖、拒绝抢占及输入变化提示。"""

    def test_registration_failure_cleans_started_process(self):
        """启动成功后源码读取失败必须回收进程，回收失败单独报告。"""
        for cleanup_status in ("stopped", "failed"):
            with self.subTest(cleanup=cleanup_status), patch.dict(service._sessions, {}, clear=True), patch.object(
                service, 'backend_launch_lock', return_value=nullcontext()
            ), patch.object(service, 'runtime_agents', return_value={'agents': [{'agentId': 'assistant'}]}), patch.object(
                service, '_launch_runtime_locked', return_value={'status': 'running', 'launch_id': 'launch',
                    'preview_url': 'http://127.0.0.1:32100', 'database_required': True,
                    'agent_ids': ['assistant'], 'debug_expires_at': 9999999999}
            ), patch.object(service, 'runtime_source_fingerprint', side_effect=OSError('read failed')), patch.object(
                service, 'stop_runtime', return_value={'status': cleanup_status}
            ) as stop:
                expected = OSError if cleanup_status == 'stopped' else RuntimeError
                with self.assertRaises(expected):
                    service.start_debug('/tmp/debug-test', 'owner', 'null', 'assistant')
                stop.assert_called_once_with(Path('/tmp/debug-test').resolve(), profile='debug')
                self.assertFalse(service._sessions)

    def test_start_prepares_dependencies_and_rejects_duplicate(self):
        """首次启动不依赖单测安装环境，同拥有者重复启动也不能覆盖活跃会话。"""
        with patch.dict(service._sessions, {}, clear=True), patch.object(
            service, 'backend_launch_lock', return_value=nullcontext()
        ), patch.object(service, 'runtime_agents', return_value={'agents': [{'agentId': 'assistant'}]}), patch.object(
            service, 'runtime_source_fingerprint', return_value='source'
        ), patch.object(service, '_launch_runtime_locked', return_value={
            'status': 'running', 'launch_id': 'launch', 'preview_url': 'http://127.0.0.1:32100',
            'database_required': True, 'agent_ids': ['assistant'], 'debug_expires_at': 9999999999,
        }) as launch:
            state, credential = service.start_debug('/tmp/debug-test', 'owner', 'null', 'assistant')
            self.assertTrue(state['active'])
            self.assertNotIn('token', state)
            self.assertTrue(credential['token'])
            self.assertEqual(credential['expiresAt'], 9999999999)
            self.assertIs(launch.call_args.kwargs['skip_install'], False)
            with self.assertRaisesRegex(ValueError, '已有调试进程'):
                service.start_debug('/tmp/debug-test', 'owner', 'null', 'assistant')
            self.assertEqual(launch.call_count, 1)

    def test_snapshot_reports_changed_code_without_exposing_credentials(self):
        """状态查询只报告所有权和重新启动提示，不泄漏凭据、不重新启动。"""
        root = str(Path('/tmp/debug-test').resolve())
        session = service.DebugSession('owner', 'launch', 'http://127.0.0.1:32100',
                                       9999999999, True, ['assistant'], ['assistant'], 'before')
        with patch.dict(service._sessions, {root: session}, clear=True), patch.object(
            service, 'backend_launch_lock', return_value=nullcontext()
        ), patch.object(service, 'runtime_health', return_value=(True, {})), patch.object(
            service, 'runtime_source_fingerprint', return_value='after'
        ):
            result = service.debug_snapshot(root, 'other')
            self.assertTrue(result['restartRequired'])
            self.assertFalse(result['ownedByCaller'])
            self.assertNotIn('token', result)
            with self.assertRaisesRegex(ValueError, '不能停止其他会话'):
                service.stop_debug(root, 'other')
