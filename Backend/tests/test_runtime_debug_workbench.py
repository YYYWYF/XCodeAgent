"""调试启动的依赖准备与拥有者边界，不启动进程或调用模型。"""
from contextlib import nullcontext
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from app.services import agent_runtime_workbench as service


class RuntimeDebugWorkbenchTests(TestCase):
    """验证首次调试安装依赖、拒绝抢占及输入变化提示。"""

    def test_expired_session_is_reclaimed_without_health_or_replay(self) -> None:
        """过期会话先回收且不探测旧凭据；其他观察者不能触发模型或重新启动。"""
        root = str(Path('/tmp/debug-expiry-test').resolve())
        session = service.DebugSession('owner', 'launch', 'http://127.0.0.1:32100',
                                       100, False, ['assistant'], ['assistant'], 'source', token='private-test-token')
        with patch.dict(service._sessions, {root: session}, clear=True), patch.object(
            service, 'backend_launch_lock', return_value=nullcontext()
        ), patch.object(service.time, 'time', return_value=101), patch.object(
            service, 'runtime_health', return_value=(True, 'ready')
        ) as health, patch.object(service, 'stop_runtime', return_value={'status': 'stopped'}) as stop:
            self.assertEqual(service.debug_snapshot(root, 'observer'), {'active': False})
            health.assert_not_called()
            stop.assert_called_once_with(Path(root), profile='debug')
            self.assertNotIn(root, service._sessions)
            self.assertNotIn('private-test-token', repr(session))

    def test_failed_cleanup_preserves_owner_for_explicit_retry(self) -> None:
        """回收失败不谎报停止或释放拥有者；下一次显式操作仍能安全重试。"""
        root = str(Path('/tmp/debug-expiry-test').resolve())
        session = service.DebugSession('owner', 'launch', 'http://127.0.0.1:32100',
                                       100, False, ['assistant'], ['assistant'], 'source')
        with patch.dict(service._sessions, {root: session}, clear=True), patch.object(
            service, 'backend_launch_lock', return_value=nullcontext()
        ), patch.object(service.time, 'time', return_value=101), patch.object(
            service, 'stop_runtime', side_effect=[{'status': 'failed', 'message': '回收失败'}, {'status': 'stopped'}]
        ):
            with self.assertRaisesRegex(ValueError, '回收失败'):
                service.debug_snapshot(root, 'observer')
            self.assertIs(service._sessions[root], session)
            self.assertEqual(service.stop_debug(root, 'owner'), {'active': False})

    def test_expiry_during_health_does_not_publish_active_session(self) -> None:
        """健康探测期间跨过到期边界时复核时钟，不能返回已失效的活动会话。"""
        root = str(Path('/tmp/debug-expiry-test').resolve())
        session = service.DebugSession('owner', 'launch', 'http://127.0.0.1:32100',
                                       100, False, ['assistant'], ['assistant'], 'source')
        with patch.dict(service._sessions, {root: session}, clear=True), patch.object(
            service, 'backend_launch_lock', return_value=nullcontext()
        ), patch.object(service.time, 'time', side_effect=[99, 101]), patch.object(
            service, 'runtime_health', return_value=(True, 'ready')
        ), patch.object(service, 'stop_runtime', return_value={'status': 'stopped'}):
            self.assertEqual(service.debug_snapshot(root, 'owner'), {'active': False})
            self.assertNotIn(root, service._sessions)

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
