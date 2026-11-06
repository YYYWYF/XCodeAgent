"""实时连接诊断只读，不能把服务健康升级为发送或业务验收授权。"""

from pathlib import Path
import asyncio
import json
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tests.test_runtime_endpoint_contracts import agent
from app.services.agent_ui_runtime_connection import connection_snapshot


class RuntimeConnectionTests(TestCase):
    """覆盖当前事实、探测边界和不可泄露的输入。"""

    def setUp(self) -> None:
        """使用正式合同夹具及本轮启动事实，不创建模型或服务。"""
        self.root = Path('/tmp/connection-test').resolve()
        self.frontend = {'status': 'running', 'port': 3000}
        self.runtime = {'status': 'running', 'launchId': 'current'}
        self.contract = agent('assistant', 'chat')
        self.contract['agentSettings']['context'] = {'pageId': 'chat'}
        for name, value in (
            ('runtime_check_plan', {'confirmed': True}),
            ('agent_contract_index', {'assistant': self.contract}),
            ('load_application_json', {'auth': {'enable': False}, 'authorization': {'enabled': False},
                                       'private': 'never-expose'}),
        ):
            target = patch('app.services.agent_ui_runtime_connection.' + name, return_value=value)
            target.start()
            self.addCleanup(target.stop)

    def test_proxy_health_never_authorizes_sending_or_browser_session(self) -> None:
        """健康通过仍保留外部缺口，且探测地址不能取自前端不可信 URL。"""
        self.frontend['url'] = 'https://untrusted.example/path'
        with patch('app.services.agent_ui_runtime_connection.runtime_health', return_value=(True, 'ready')) as health:
            value = connection_snapshot(self.root, self.frontend, self.runtime)
        self.assertEqual(value['proxyHealth']['status'], 'ready')
        self.assertFalse(value['sendEnabled'])
        checks = {item['id']: item for item in value['checks']}
        for key in ('context', 'run_identity', 'send_admission'):
            self.assertEqual(checks[key]['status'], 'blocked')
        self.assertEqual(checks['same_origin_session']['status'], 'not_verified')
        self.assertEqual(checks['real_conversation']['status'], 'not_verified')
        self.assertNotIn('never-expose', str(value))
        self.assertEqual(health.call_args.args, ('http://127.0.0.1:3000', 'current'))
        self.assertEqual(health.call_args.kwargs['expected_agent_ids'], ['assistant'])
        self.assertEqual(health.call_args.kwargs['expected_application'], self.root / 'agent-runtime')

    def test_stopped_services_or_invalid_port_never_probe(self) -> None:
        """缺少当前进程或合法端口时不访问网络，不推断历史会话可用。"""
        for frontend in ({'status': 'stopped', 'port': 3000}, {'status': 'running', 'port': True},
                         {'status': 'running', 'port': 0}, {'status': 'running', 'port': '3000'}):
            with self.subTest(frontend=frontend), patch('app.services.agent_ui_runtime_connection.runtime_health') as health:
                value = connection_snapshot(self.root, frontend, self.runtime)
                self.assertEqual(value['proxyHealth']['status'], 'not_verified')
                health.assert_not_called()

    def test_failed_identity_is_visible_and_not_persisted(self) -> None:
        """报告当前探测失败，不能覆盖原启动事实或缓存成功。"""
        with patch('app.services.agent_ui_runtime_connection.runtime_health', return_value=(False, '应用身份不一致')):
            value = connection_snapshot(self.root, self.frontend, self.runtime)
        self.assertEqual(value['proxyHealth'], {'status': 'blocked', 'reason': '应用身份不一致'})
        self.assertEqual(self.runtime, {'status': 'running', 'launchId': 'current'})

    def test_plain_text_preview_needs_current_launch_and_healthy_identity(self) -> None:
        """静态合同或健康成功都不能独自声明真实预览已启用。"""
        self.contract['agentSettings']['context'] = {'description': '只读用户消息。'}
        for marker, ready, expected in ((None, True, False),
                ({'mode': 'official-mock', 'agentId': 'assistant'}, True, False),
                ({'mode': 'real-text', 'agentId': 'other'}, True, False),
                ({'mode': 'real-text', 'agentId': 'assistant'}, False, False),
                ({'mode': 'formal-text', 'agentId': 'other'}, True, False),
                ({'mode': 'formal-text', 'agentId': 'assistant'}, False, False),
                ({'mode': 'formal-text', 'agentId': 'assistant'}, True, True),
                ({'mode': 'real-text', 'agentId': 'assistant'}, True, True)):
            with self.subTest(marker=marker, ready=ready), patch(
                'app.services.agent_ui_runtime_connection.runtime_health', return_value=(ready, 'health')
            ):
                value = connection_snapshot(self.root, {**self.frontend, 'agentUiPreview': marker}, self.runtime)
                self.assertEqual(value['plainTextPreview'], expected)
                self.assertIs(value['sendEnabled'], False)

    def test_invalid_contract_returns_safe_failure_without_probe(self) -> None:
        """正式合同损坏或多 Agent 不能任意选择目标，错误详情不回传。"""
        with patch('app.services.agent_ui_runtime_connection.runtime_check_plan', side_effect=ValueError('private-path')):
            value = connection_snapshot(self.root, self.frontend, self.runtime)
        self.assertFalse(value['sendEnabled'])
        self.assertEqual(value['proxyHealth']['status'], 'not_verified')
        self.assertNotIn('private-path', str(value))

    def test_agui_get_projects_diagnostics_without_persisting(self) -> None:
        """原 AG-UI 读取交付完整生命周期与诊断，不更新正式或启动记录。"""
        from app.protocols.preview_runtime import build_preview_runtime_stream

        async def collect(workspace: str) -> list[dict]:
            """消费真实协议编码，不使用手工事件替身。"""
            events: list[dict] = []
            payload = {'threadId': 'connection-test', 'runId': 'read', 'forwardedProps': {
                'previewRuntime': {'workspace': workspace, 'action': 'get', 'includeLogs': False}}}
            async for frame in build_preview_runtime_stream(payload=payload):
                events.extend(json.loads(line[6:]) for line in frame.splitlines() if line.startswith('data: '))
            return events

        with TemporaryDirectory() as workspace, patch(
            'app.services.preview_runtime_state.preview_topology_type', return_value='agent_runtime_direct'
        ), patch('app.services.preview_runtime_state.write_record') as write:
            config = Path(workspace) / '.devagentstudio'
            config.mkdir()
            (config / 'application.json').write_text('{}', encoding='utf-8')
            events = asyncio.run(collect(workspace))
        self.assertEqual(events[0]['type'], 'RUN_STARTED')
        self.assertEqual(events[-1]['type'], 'RUN_FINISHED')
        self.assertIn('STATE_SNAPSHOT', [event['type'] for event in events])
        report = events[-1]['result']['previewRuntime']['runtime']['agentUiConnection']
        self.assertFalse(report['sendEnabled'])
        self.assertEqual(report['proxyHealth']['status'], 'not_verified')
        write.assert_not_called()
