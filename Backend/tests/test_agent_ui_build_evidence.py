"""真实磁盘共享来源的范围、身份和来源失效回归。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
from unittest import TestCase

from app.services.agent_ui_build_contract import current_agent_ui_build_contract
from app.services.agent_ui_build_evidence import load_build_evidence
from app.services.agent_ui_dependency_plan import digest
from app.services.business_acceptance_verifier import _current_source_hash
from tests.agent_ui_build_fixtures import shared_build_target


class SharedBuildEvidenceTests(TestCase):
    """只替代昂贵 SDK/命令，确认和转入凭据通过原 owner 真实落盘。"""

    def setUp(self) -> None:
        """为每个来源变化准备独立目标，避免测试之间共享变更。"""
        self.temp = TemporaryDirectory(prefix='T25 来源 中文 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.record = shared_build_target(self.root)

    def test_page_changes_and_new_build_do_not_invalidate_shared_ownership(self) -> None:
        """页面继续开发无需重转入；dist 不能充当静态来源或真实行为证据。"""
        before = load_build_evidence(self.root)
        self.assertEqual(before['host']['connection'], 'plain_text')
        provider = self.root / 'frontend/src/extensions/AgentUiHostProvider.tsx'
        self.assertIn('endpoint="/agents/assistant/run"', provider.read_text())
        page = self.root / 'frontend/src/pages/Home.tsx'
        page.parent.mkdir(parents=True)
        page.write_text('export default function Home(){return <main>新业务页面</main>}')
        (self.root / 'frontend/dist/test.css').write_text('new build')
        self.assertEqual(load_build_evidence(self.root), before)
        contract = current_agent_ui_build_contract(self.root, 'chat')
        self.assertEqual(contract['presentation']['props']['welcome']['title'], '定制欢迎')
        self.assertEqual(_current_source_hash({'artifact': 'agent_ui_build_contract', 'target_id': 'chat'},
            {}, workspace_root=self.root), digest(contract))

    def test_shared_customization_change_invalidates_current_source(self) -> None:
        """应用层、展示和 BASE 变化均须重新审阅，不能仅凭旧成功状态通过。"""
        for name in ('src/正式助手/application/project-config.tsx',
                     'src/正式助手/design/presentation.json', '.agent-ui/source-lock.json',
                     'src/extensions/providers.ts'):
            path = self.root / 'frontend' / name
            before = path.read_text()
            path.write_text(before + '\n// changed')
            with self.subTest(name=name), self.assertRaises(ValueError):
                load_build_evidence(self.root)
            path.write_text(before)

    def test_new_shared_file_cannot_evade_recorded_ownership(self) -> None:
        """共享目录新增实现同样改变来源，而不是只比较已有路径。"""
        (self.root / 'frontend/src/正式助手/extra.tsx').write_text('export {}')
        with self.assertRaises(ValueError):
            load_build_evidence(self.root)

    def test_changed_package_and_formal_markdown_are_rejected(self) -> None:
        """实际包身份和用户 Markdown 修改都会使旧证据过期。"""
        path = self.root / 'frontend/node_modules/@za38.03/agent-ui-runtime/package.json'
        before = path.read_text()
        path.write_text(before + '\n')
        with self.assertRaises(ValueError):
            load_build_evidence(self.root)
        path.write_text(before)
        markdown = self.root / '.devagentstudio/specs/ui-designs.md'
        markdown.write_text(markdown.read_text() + '\n用户修订\n')
        with self.assertRaises(ValueError):
            load_build_evidence(self.root)

    def test_failed_record_or_forged_plan_cannot_be_current(self) -> None:
        """失败状态和计划内容篡改不能伪装成 verified。"""
        path = self.root / self.record['recordPath']
        before = json.loads(path.read_text())
        value = {**before, 'state': 'failed'}
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            load_build_evidence(self.root)
        before['plan']['source']['revision'] = '1' * 64
        path.write_text(json.dumps(before))
        with self.assertRaises(ValueError):
            load_build_evidence(self.root)

    def test_resigned_connection_claim_cannot_override_current_conditions(self) -> None:
        """即使重算计划摘要，记录自报的连接就绪仍须与当前正式事实重算结果一致。"""
        original = self.root / self.record['recordPath']
        for field, value in (('sendEnabled', True), ('checks', []), ('endpoint', '/agents/foreign/run')):
            record = json.loads(original.read_text())
            record['plan']['hostIntegration']['connectionRequirements'][field] = value
            plan_id = digest({key: item for key, item in record['plan'].items() if key != 'planId'})
            record['planId'] = record['plan']['planId'] = plan_id
            directory = original.parent.parent / plan_id
            directory.mkdir(exist_ok=True)
            (directory / 'record.json').write_text(json.dumps(record))
            pointer = self.root / '.devagentstudio/runtime/agent-ui-transfer/current.json'
            pointer.write_text(json.dumps({'planId': plan_id}))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, '连接条件'):
                load_build_evidence(self.root)
