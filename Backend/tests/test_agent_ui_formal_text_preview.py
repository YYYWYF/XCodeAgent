"""正式纯文本源码必须保持原样，开发预览只负责同源服务和环境变量。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.services.agent_ui_dependency_plan import sha256
from app.services.agent_ui_host_integration import PROVIDER
from app.services.agent_ui_managed_preview import managed_preview_command
from tests.test_runtime_endpoint_contracts import agent, plan


class FormalTextPreviewTests(TestCase):
    """复验当前正式能力、逐文件归属与本轮 Runtime，不偷偷改写正式 endpoint。"""

    def setUp(self) -> None:
        """准备受归属保护的正式 Host 与只读工具环境。"""
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'frontend'
        self.provider = self.target / PROVIDER
        self.provider.parent.mkdir(parents=True)
        self.provider.write_text('return <SharedAgentApplication agentId="assistant" endpoint="/agents/assistant/run" />')
        self.pointer = self.root / '.devagentstudio/runtime/agent-ui-transfer/current.json'
        self.pointer.parent.mkdir(parents=True)
        self.pointer.write_text(json.dumps({'planId': 'a' * 64}))
        self.record = {'state': 'verified', 'plan': {'target': {'sourceRoot': 'src/agent-ui'},
            'hostIntegration': {'agentId': 'assistant', 'connection': 'plain_text'}},
            'owned': {PROVIDER: sha256(self.provider.read_bytes())}}
        (self.root / 'record.json').write_text(json.dumps(self.record))
        self.contract = agent('assistant', 'chat')
        technical = plan()
        technical['pages'] = [{'pageId': 'chat'}]
        technical['agent_contracts'] = [self.contract]
        self.application = {'auth': {'enable': False}, 'authorization': {'enabled': False}}
        inspection = SimpleNamespace(installation_root=self.root, creator_version='0.0.1',
            package_json_sha256='b' * 64, node_executable=Path('/node'))
        patches = (
            patch('app.services.agent_ui_managed_preview.transfer_directory', return_value=self.root),
            patch('app.services.agent_ui_managed_preview.transfer_ownership',
                  side_effect=lambda *_args: {PROVIDER: sha256(self.provider.read_bytes())}),
            patch('app.services.agent_ui_managed_preview.runtime_check_plan', return_value=technical),
            patch('app.services.agent_ui_managed_preview.load_application_json', return_value=self.application),
            patch('app.services.agent_ui_design_workflow.design_environment', return_value=(inspection, {})),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_formal_source_uses_non_injecting_preview_mode(self) -> None:
        """正式 endpoint 已进入源码，开发启动不得回到 Mock 或空连接替换。"""
        before = self.provider.read_bytes()
        command = managed_preview_command(self.root, self.target, 'rsbuild dev', runtime_public_url='http://127.0.0.1:9000')
        self.assertIsNotNone(command)
        options = json.loads(command[-1])
        self.assertEqual(options['mode'], 'formal-text')
        self.assertEqual(options['endpoint'], '/agents/assistant/run')
        self.assertEqual(self.provider.read_bytes(), before)

    def test_changed_capability_or_missing_runtime_cannot_open_formal_chat(self) -> None:
        """正式规划扩大权限或当前 Runtime 不存在时必须停止，不能沿用旧连接。"""
        with self.assertRaises(ValueError):
            managed_preview_command(self.root, self.target, 'rsbuild dev')
        self.application['authorization']['enabled'] = True
        with self.assertRaises(ValueError):
            managed_preview_command(self.root, self.target, 'rsbuild dev', runtime_public_url='http://127.0.0.1:9000')

    def test_changed_provider_cannot_reuse_owned_connection(self) -> None:
        """即使调用方声明归属，修改后的 Provider 也不能复用旧摘要。"""
        self.provider.write_text(self.provider.read_text() + '\n// user edit')
        with self.assertRaises(ValueError):
            managed_preview_command(self.root, self.target, 'rsbuild dev', runtime_public_url='http://127.0.0.1:9000')
