"""共享 Host 通过模板扩展入口接线，计划审阅不能执行或覆盖用户 Provider。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import shutil
from unittest.mock import patch

from app.services.agent_ui_dependency_plan import REPOSITORY
from app.services.agent_ui_host_integration import plan_host_integration, current_host_contract
from tests.test_runtime_endpoint_contracts import agent, plan


class AgentUiHostIntegrationTests(unittest.TestCase):
    """验证路由/身份正式来源、逐文件审阅及未知模板的保守失败。"""

    def setUp(self) -> None:
        """准备最小模板公开扩展及同一 Agent 的正式页面绑定。"""
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'frontend'
        self.source = self.root / 'source'
        (self.source / 'src/design').mkdir(parents=True)
        for name in ('SharedApplication.tsx', 'SharedPage.tsx', 'runtimeResults.ts', 'useRuntimeResult.ts'):
            shutil.copy2(REPOSITORY / 'tools/agent-ui-design' / name, self.source / 'src/design' / name)
        (self.source / 'package.json').write_text('{}')
        (self.source / 'node_modules').symlink_to(REPOSITORY / 'tools/agent-ui-consumer/node_modules', target_is_directory=True)
        self.state = {'technical_plan': plan(), 'application_config': {
            'auth': {'enable': False}, 'authorization': {'enabled': False}}}
        self.state['technical_plan']['agent_contracts'] = [agent('assistant', 'orders')]
        for name in ('src/extensions/providers.ts', 'src/providers/AppProviders.tsx',
                     'src/routes/pageRegistry.ts', 'src/utils/pageRouteTree.ts', 'src/platform/identity/IdentityContext.tsx'):
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('export {}')
        (self.target / 'src/providers/AppProviders.tsx').write_text(
            "import { extensionProviders } from '@/extensions/providers'\n"
            "import { IdentityProvider } from '@/platform/identity/IdentityContext'\n"
            "export function AppProviders({children}) {\n"
            "  const content = extensionProviders.reduceRight((current, Provider) => <Provider>{current}</Provider>, children)\n"
            "  return <IdentityProvider>{content}</IdentityProvider>\n}\n")
        self.registry = self.target / 'src/extensions/providers.ts'
        self.registry.write_text("import type { ComponentType, PropsWithChildren } from 'react';\n"
            "import { ExistingProvider } from './existing';\n"
            "// 用户的原 Provider 保留\n"
            "export const extensionProviders: ComponentType<PropsWithChildren>[] = [ExistingProvider];\n")

    def prepare(self) -> dict:
        """只读派生可纳入 T23 审阅的 Host 文件，不改变磁盘。"""
        return plan_host_integration(self.state, target=self.target, source=self.source,
            source_root='src/正式助手', page_ids=['orders'], workspace=self.root)

    def test_preserves_existing_provider_and_derives_formal_endpoint(self) -> None:
        """接线保留已有 Provider/注释，仅从正式 Agent 绑定产生地址。"""
        before = self.registry.read_text()
        result = self.prepare()
        self.assertEqual(result['status'], 'reviewable', result)
        self.assertEqual(result['endpoint'], '/agents/assistant/run')
        self.assertEqual(self.registry.read_text(), before)
        files = result['files']
        self.assertIn('ExistingProvider', files['src/extensions/providers.ts'])
        self.assertIn('// 用户的原 Provider 保留', files['src/extensions/providers.ts'])
        self.assertIn('SharedAgentHostProvider', files['src/extensions/providers.ts'])
        provider = files['src/extensions/AgentUiHostProvider.tsx']
        self.assertIn('APP_PAGE_ROUTE_TREE', provider)
        self.assertIn('useLocation', provider)
        self.assertIn('sessionKey=', provider)
        self.assertNotIn('getRunContext=', provider)
        self.assertEqual(result['connection'], 'plain_text')
        self.assertIn('endpoint="/agents/assistant/run"', provider)
        self.assertIn('agentId="assistant"', provider)

    def test_business_capabilities_keep_formal_connection_disabled(self) -> None:
        """纯文本例外不能开放页面数据、工具、状态、审批或登录身份。"""
        from copy import deepcopy
        original = deepcopy(self.state)
        for feature in ('context', 'tools', 'state', 'interrupt', 'auth'):
            self.state = deepcopy(original)
            contract = self.state['technical_plan']['agent_contracts'][0]
            if feature == 'context':
                contract['agentSettings']['context'] = {'pageId': 'orders'}
            elif feature == 'tools':
                contract['agentSettings']['tools']['enabled'] = True
            elif feature == 'state':
                contract['interactionContract']['publicStateSchema'] = {}
            elif feature == 'interrupt':
                contract['interactionContract']['interactionKinds'] = ['approval_required']
            else:
                self.state['application_config']['auth']['enable'] = True
            with self.subTest(feature=feature):
                result = self.prepare()
                if result['status'] == 'blocked':
                    self.assertEqual(result['files'], {})
                else:
                    self.assertEqual(result['connection'], 'blocked_T27')
                    self.assertIn('endpoint=""', result['files']['src/extensions/AgentUiHostProvider.tsx'])

    def test_description_only_context_remains_plain_text(self) -> None:
        """提示描述不等同于业务页面数据，正式接线只使用当前 Agent 固定地址。"""
        self.state['technical_plan']['agent_contracts'][0]['agentSettings']['context'] = {'description': '回答用户问题。'}
        result = self.prepare()
        self.assertEqual(result['connection'], 'plain_text')
        self.assertIn('endpoint="/agents/assistant/run"', result['files']['src/extensions/AgentUiHostProvider.tsx'])

    def test_connection_conditions_are_read_only_and_follow_application_config(self) -> None:
        """连接报告进入原审阅计划；产品投影不能覆盖正式身份配置或开放发送。"""
        first = self.prepare()
        requirements = first['connectionRequirements']
        self.assertIs(requirements['sendEnabled'], False)
        self.assertEqual(requirements['endpoint'], first['endpoint'])
        checks = {item['id']: item for item in requirements['checks']}
        self.assertEqual(checks['runtime_identity']['status'], 'supported')
        self.assertEqual(checks['same_origin_session']['status'], 'not_verified')
        self.state['application_config']['authorization']['enabled'] = True
        self.state['product_plan'] = {'authorization': {'enabled': False}}
        changed = self.prepare()
        self.assertEqual(changed['status'], 'reviewable')
        self.assertIn('authorization.enabled=false', changed['reason'])
        self.assertIn('endpoint=""', changed['files']['src/extensions/AgentUiHostProvider.tsx'])
        self.assertNotEqual(changed['connectionRequirements'], requirements)

    def test_auth_uses_application_config_and_identity_context(self) -> None:
        """应用 auth.enable 为唯一配置源，不能使用产品投影覆盖。"""
        self.state['application_config']['auth']['enable'] = True
        self.state['product_plan'] = {'auth': {'enable': False}}
        provider = self.prepare()['files']['src/extensions/AgentUiHostProvider.tsx']
        self.assertIn('useContext(IdentityContext)', provider)
        self.assertIn('identity?.userId', provider)
        self.assertNotIn("sessionKey='anonymous'", provider)

    def test_missing_result_modules_requires_new_confirmed_design(self) -> None:
        """旧设计缺少新接入模块时在计划阶段阻断，不先写入不能编译的新 Provider。"""
        (self.source / 'src/design/runtimeResults.ts').unlink()
        result = self.prepare()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['files'], {})
        self.assertIn('重新生成并确认', result['reason'])

    def test_missing_hook_or_ambiguous_registry_is_blocked(self) -> None:
        """缺模板扩展或动态注册表不能改写 Router/App 作为兜底。"""
        self.registry.write_text('export const extensionProviders = buildProviders()')
        result = self.prepare()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['files'], {})
        self.registry.unlink()
        self.assertEqual(self.prepare()['status'], 'unavailable')

    def test_multiple_agents_or_wrong_page_binding_are_blocked(self) -> None:
        """共享试点不自动选择 Agent，页面与实际会话身份必须对应。"""
        self.state['technical_plan']['agent_contracts'].append(agent('other', 'policy'))
        self.assertEqual(self.prepare()['status'], 'blocked')
        self.state['technical_plan']['agent_contracts'] = [agent('assistant', 'policy')]
        self.assertEqual(self.prepare()['status'], 'blocked')

    def test_import_without_mounted_extension_cannot_claim_wiring(self) -> None:
        """仅导入或保存 Provider 名称不能证明模板已挂载扩展。"""
        app = self.target / 'src/providers/AppProviders.tsx'
        app.write_text("import { extensionProviders } from '@/extensions/providers'\n"
            "export function AppProviders({children}) { return <>{children}</> }")
        self.assertEqual(self.prepare()['status'], 'blocked')

    def test_type_only_host_import_is_not_a_runtime_registration(self) -> None:
        """类型导入不能被当作实际运行时 Host 接线。"""
        self.registry.write_text("import type { SharedAgentHostProvider } from './AgentUiHostProvider'\n"
            "export const extensionProviders = [SharedAgentHostProvider]")
        self.assertEqual(self.prepare()['status'], 'blocked')

    def test_repeated_projection_does_not_register_twice(self) -> None:
        """重新审阅是幂等 AST 修改，不增加第二个共享会话 Provider。"""
        first = self.prepare()
        self.registry.write_text(first['files']['src/extensions/providers.ts'])
        second = self.prepare()
        self.assertEqual(second['files']['src/extensions/providers.ts'], self.registry.read_text())

    def test_page_instructions_do_not_grant_host_or_transport_writes(self) -> None:
        """正式页面只导入布局占位/显隐 hook，不能绕过待接入的真实连接。"""
        from app.agents.frontend.generator import _agent_ui_runtime_instruction
        from app.agents.frontend.runtime_instruction import runtime_frontend_instruction
        contract = {'mode': 'shared_host', 'pageId': 'orders', 'host': {
            'componentModule': '@/正式助手/design/SharedApplication', 'connection': 'blocked_T27'}}
        instruction = _agent_ui_runtime_instruction([contract])
        self.assertIn('SharedAgentPage', instruction)
        self.assertIn('useSharedAgentPanel', instruction)
        self.assertIn('never become page task allowed_paths', instruction)
        self.assertNotIn('AGENT_UI_CONFIG', instruction)
        self.assertIn('blocked_T27', runtime_frontend_instruction(self.state['technical_plan'],
            [{'unit_id': 'page:orders', 'source_refs': {'agent_ui': contract}}]))
        with self.assertRaisesRegex(ValueError, '混用'):
            _agent_ui_runtime_instruction([contract, {'mode': 'runtime'}])

    def test_generation_requires_current_verified_transfer(self) -> None:
        """来源缺失和过期都不能给页面任务一个虚假的共享入口。"""
        with self.assertRaisesRegex(ValueError, '尚未转入'):
            current_host_contract(self.root)
        pointer = self.root / '.devagentstudio/runtime/agent-ui-transfer/current.json'
        pointer.parent.mkdir(parents=True)
        pointer.write_text('{"planId":"fixture"}')
        record = {'state': 'stale'}
        with patch('app.services.agent_ui_transfer_records.read_transfer', return_value=record):
            with self.assertRaisesRegex(ValueError, '过期'):
                current_host_contract(self.root)
        record = {'state': 'verified', 'planId': 'fixture', 'plan': {
            'hostIntegration': {'status': 'reviewable', 'pageIds': ['orders']},
            'target': {'sourceRoot': 'src/正式助手'}}}
        with patch('app.services.agent_ui_transfer_records.read_transfer', return_value=record):
            self.assertEqual(current_host_contract(self.root)['componentModule'], '@/正式助手/design/SharedApplication')


if __name__ == '__main__':
    unittest.main()
