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
                    'agentUiObservation': {'sessionId': 'b' * 32, 'source': 'formal-text', 'launchId': 'launch', 'identity': capture_identity(self.root)}}
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

    def test_unowned_page_and_package_implementation_changes_invalidate_revision(self) -> None:
        """正文新增、同版本包实现变化及配置变更均进入当前完整受检摘要。"""
        original = capture_identity(self.root)
        page = self.target / 'src/pages/Home.tsx'
        page.parent.mkdir()
        page.write_text('private-page')
        changed = capture_identity(self.root)
        self.assertNotEqual(original['revisionSha256'], changed['revisionSha256'])
        runtime = self.target / 'node_modules/@za38.03/agent-ui-runtime/index.js'
        runtime.write_text('private-package')
        repacked = capture_identity(self.root)
        self.assertNotEqual(changed['packages'], repacked['packages'])
        self.assertNotEqual(changed['revisionSha256'], repacked['revisionSha256'])
        self.assertNotIn('private-page', str(repacked))
        self.assertNotIn('private-package', str(repacked))
        plan = self.root / '.devagentstudio/plans/technical-plan.json'
        plan.parent.mkdir()
        plan.write_text('{"privateConfig":true}')
        self.assertNotEqual(repacked['revisionSha256'], capture_identity(self.root)['revisionSha256'])

    def test_live_runtime_must_match_captured_launch_and_workspace(self) -> None:
        """相同磁盘内容不能证明重启后的另一个 Python launch 或工程属于原会话。"""
        frontend = {'status': 'running', 'agentUiPreview': {'mode': 'formal-business', 'agentId': 'assistant'},
                    'agentUiObservation': {'sessionId': 'b' * 32, 'source': 'formal-business', 'launchId': 'launch-a',
                                           'identity': capture_identity(self.root)}}
        self.assertTrue(observation_snapshot(self.root, frontend, {'launchId': 'launch-a'}, 'assistant', True)['identityMatches'])
        self.assertFalse(observation_snapshot(self.root, frontend, {'launchId': 'launch-b'}, 'assistant', True)['identityMatches'])
        frontend['agentUiPreview']['mode'] = 'official-mock'
        self.assertFalse(observation_snapshot(self.root, frontend, {'launchId': 'launch-a'}, 'assistant', True)['identityMatches'])

    def test_secrets_and_runtime_outputs_do_not_enter_revision(self) -> None:
        """凭据和运行输出不参与源码修订，符号链接源码则拒绝签署。"""
        original = capture_identity(self.root)
        (self.target / '.env').write_text('TOKEN=private-token')
        output = self.target / 'dist'
        output.mkdir()
        (output / 'index.js').write_text('output')
        self.assertEqual(original, capture_identity(self.root))
        (self.target / 'src/link.ts').symlink_to(self.source)
        self.assertEqual(capture_identity(self.root)['status'], 'unavailable')
