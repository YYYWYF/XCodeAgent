"""T28 来源身份只读、有界，不能复用其他启动或泄露源码内容。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.services.agent_ui_observation import capture_identity, observation_snapshot


class ObservationTests(TestCase):
    """使用最小当前转入记录及实际包文件验证磁盘/启动身份分离。"""

    def setUp(self) -> None:
        """建立唯一 Agent 的当前转入及四个实际安装包。"""
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'frontend'
        self.source = self.target / 'src/agent-ui/design/SharedHost.tsx'
        self.source.parent.mkdir(parents=True)
        self.source.write_text('private-source-content')
        transfer = self.root / '.devagentstudio/runtime/agent-ui-transfer'
        record = transfer / ('a' * 64)
        record.mkdir(parents=True)
        (transfer / 'current.json').write_text(json.dumps({'planId': 'a' * 64}))
        (record / 'record.json').write_text(json.dumps({'planId': 'a' * 64,
            'plan': {'target': {'sourceRoot': 'src/agent-ui'}},
            'owned': {'src/agent-ui/design/SharedHost.tsx': 'old-record-digest'}}))
        (self.target / 'pnpm-lock.yaml').write_text('lockfileVersion: 9')
        for name in ('core', 'renderer', 'runtime', 'plugins'):
            directory = self.target / 'node_modules/@za38.03' / ('agent-ui-' + name)
            directory.mkdir(parents=True)
            (directory / 'package.json').write_text(json.dumps({'name': '@za38.03/agent-ui-' + name, 'version': '0.0.1'}))

    def test_source_change_and_same_version_repack_invalidate_started_identity(self) -> None:
        """真实字节变化必须可见，同版本号不能掩盖换包或手工源码变更。"""
        original = capture_identity(self.root)
        self.assertEqual(original['status'], 'available')
        self.assertNotIn('private-source-content', str(original))
        self.source.write_text('edited')
        changed = capture_identity(self.root)
        self.assertNotEqual(original['sourceSha256'], changed['sourceSha256'])
        metadata = self.target / 'node_modules/@za38.03/agent-ui-runtime/package.json'
        metadata.write_text(metadata.read_text() + '\n')
        repacked = capture_identity(self.root)
        self.assertNotEqual(changed['packages'], repacked['packages'])

    def test_launch_source_is_never_inferred_from_endpoint_or_health_alone(self) -> None:
        """来源绑定本次受管 marker 与会话 epoch，未知来源不猜测为真实。"""
        frontend = {'status': 'running', 'agentUiPreview': {'mode': 'formal-text', 'agentId': 'assistant'},
                    'agentUiObservation': {'sessionId': 'b' * 32, 'identity': capture_identity(self.root)}}
        view = observation_snapshot(self.root, frontend, {'launchId': 'launch'}, 'assistant', True)
        self.assertEqual(view['source'], 'formal-text')
        self.assertTrue(view['identityMatches'])
        self.source.write_text('changed-after-launch')
        self.assertFalse(observation_snapshot(self.root, frontend, {'launchId': 'launch'}, 'assistant', True)['identityMatches'])
        for marker in (None, {'mode': 'replay', 'agentId': 'assistant'}, {'mode': 'formal-text', 'agentId': 'other'}):
            frontend['agentUiPreview'] = marker
            self.assertEqual(observation_snapshot(self.root, frontend, {'launchId': 'launch'}, 'assistant', True)['source'], 'unknown')

    def test_missing_identity_or_unsafe_owned_path_is_not_recovered(self) -> None:
        """不恢复旧记录、不读取越界路径，也不因坏身份影响服务诊断生命周期。"""
        record = self.root / '.devagentstudio/runtime/agent-ui-transfer' / ('a' * 64) / 'record.json'
        data = json.loads(record.read_text())
        data['owned'] = {'src/agent-ui/../../.env': 'digest'}
        record.write_text(json.dumps(data))
        self.assertEqual(capture_identity(self.root)['status'], 'unavailable')
        view = observation_snapshot(self.root, {'status': 'running'}, {'launchId': 'launch'}, 'assistant', True)
        self.assertIsNone(view['sessionId'])
        self.assertFalse(view['identityMatches'])
