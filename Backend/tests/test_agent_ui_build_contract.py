"""共享构建合同必须携带可复核来源，不以旧模板或静态检查冒充真实连接。"""

from copy import deepcopy
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from app.services.agent_ui_build_contract import project_agent_ui_build_contracts
from app.services.agent_ui_connection_requirements import connection_requirements
from tests.test_agent_integration_1008 import fixture


def build_evidence(page_id: str) -> dict:
    """提供独立于页面实现的已验证转入快照；磁盘验证另有集成回归。"""
    return {
        'transferPlanId': 'a' * 64, 'sourceRoot': 'src/正式助手',
        'host': {'agentId': 'assistant', 'pageIds': [page_id],
            'componentModule': '@/正式助手/design/SharedApplication',
            'providerPath': 'frontend/src/extensions/AgentUiHostProvider.tsx',
            'connection': 'blocked_T27', 'endpoint': '/agents/assistant/run',
            'connectionRequirements': connection_requirements(
                {'auth': {'enable': False}, 'authorization': {'enabled': False}},
                fixture()[2]['agent_contracts'][0])},
        'source': {'revision': 'b' * 64, 'sourceDigest': 'c' * 64},
        'publicApi': {'packages': [{'name': '@za38.03/agent-ui-runtime', 'version': '0.0.1'}]},
        'installation': {'lockSha256': 'd' * 64},
        'ownership': {'src/正式助手/app-ui.json': 'e' * 64},
        'presentations': {page_id: {'props': {'locale': 'zh-CN'}, 'styles': []}},
        'confirmations': {page_id: 'f' * 64}, 'formalSha256': '0' * 64,
    }


class SharedBuildContractTests(TestCase):
    """覆盖当前合同投影、来源绑定、页面展示和真实连接禁用边界。"""

    def test_agent_page_requires_current_transfer_instead_of_fixed_template(self) -> None:
        """没有工作区凭据时不得合成旧组件合同。"""
        _, product, technical = fixture()
        with self.assertRaisesRegex(ValueError, '工作区|转入'):
            project_agent_ui_build_contracts(product, technical)

    def test_shared_contract_contains_source_identity_and_presentation(self) -> None:
        """合法展示配置随共享来源进入任务，连接与业务验收保持待完成。"""
        _, product, technical = fixture()
        page_id = product['pages'][0]['pageId']
        evidence = build_evidence(page_id)
        with patch('app.services.agent_ui_build_contract.load_build_evidence', return_value=evidence):
            contract = project_agent_ui_build_contracts(product, technical, workspace=Path('/fixture'))[page_id]
        self.assertEqual(contract['mode'], 'shared_host')
        self.assertEqual(contract['host'], evidence['host'])
        self.assertEqual(contract['presentation']['props']['locale'], 'zh-CN')
        self.assertEqual(contract['evidence'], evidence)
        self.assertEqual(contract['verificationScope'], 'static_source')
        self.assertEqual(contract['connection'], 'blocked_T27')
        self.assertNotIn('config', contract)
        self.assertNotIn('runtimeAdapterPath', contract)

    def test_any_current_evidence_change_changes_contract_digest(self) -> None:
        """包、确认或展示变化使旧任务来源失效，不能沿用旧合同摘要。"""
        _, product, technical = fixture()
        page_id = product['pages'][0]['pageId']
        evidence = build_evidence(page_id)
        with patch('app.services.agent_ui_build_contract.load_build_evidence', return_value=evidence):
            before = project_agent_ui_build_contracts(product, technical, workspace=Path('/fixture'))[page_id]
        changed = deepcopy(evidence)
        changed['presentations'][page_id]['props']['locale'] = 'en-US'
        with patch('app.services.agent_ui_build_contract.load_build_evidence', return_value=changed):
            after = project_agent_ui_build_contracts(product, technical, workspace=Path('/fixture'))[page_id]
        self.assertNotEqual(before['sha256'], after['sha256'])
        self.assertEqual(before['presentation']['props']['locale'], 'zh-CN')

    def test_wrong_host_agent_or_missing_page_is_rejected(self) -> None:
        """共享入口必须对应同一个正式 Agent 和当前页面。"""
        _, product, technical = fixture()
        page_id = product['pages'][0]['pageId']
        for change in ({'agentId': 'foreign'}, {'pageIds': []}, {'connection': 'ready'}):
            evidence = build_evidence(page_id)
            evidence['host'].update(change)
            with self.subTest(change=change), patch(
                'app.services.agent_ui_build_contract.load_build_evidence', return_value=evidence,
            ), self.assertRaises(ValueError):
                project_agent_ui_build_contracts(product, technical, workspace=Path('/fixture'))

    def test_connection_conditions_are_part_of_build_source_digest(self) -> None:
        """连接条件变化必须更换任务来源，不能复用旧静态验收结果。"""
        _, product, technical = fixture()
        page_id = product['pages'][0]['pageId']
        evidence = build_evidence(page_id)
        with patch('app.services.agent_ui_build_contract.load_build_evidence', return_value=evidence):
            before = project_agent_ui_build_contracts(product, technical, workspace=Path('/fixture'))[page_id]
            evidence['host']['connectionRequirements']['checks'][0]['reason'] = '当前条件已变化'
            after = project_agent_ui_build_contracts(product, technical, workspace=Path('/fixture'))[page_id]
        self.assertNotEqual(before['sha256'], after['sha256'])
        self.assertIs(before['host']['connectionRequirements']['sendEnabled'], False)

    def test_plain_business_pages_need_no_agent_installation(self) -> None:
        """无 Agent 的普通页面不引入新前置。"""
        self.assertEqual(project_agent_ui_build_contracts({'pages': [], 'agents': []}, {}), {})

    def test_unit_projection_overwrites_old_source_and_task_extractor_rejects_legacy(self) -> None:
        """Unit 和任务消费同一共享合同，模型来源不能保留旧固定组件。"""
        from app.services.build_unit_compiler import _unit_source_refs
        from app.agents.frontend.generator import _task_agent_ui_contracts
        current = {'mode': 'shared_host', 'pageId': 'chat', 'host': build_evidence('chat')['host']}
        unit = {'source_refs': {'agent_ui': {'mode': 'runtime'}}}
        context = {'agent_ui_by_page': {'chat': current}}
        refs = _unit_source_refs('page:chat', unit, context)
        self.assertEqual(refs['agent_ui'], current)
        self.assertEqual(_task_agent_ui_contracts([{'id': 'page', 'source_refs': refs}])[0]['mode'], 'shared_host')
        self.assertNotIn('agent_ui', _unit_source_refs('page:chat', unit, {}))
        with self.assertRaises(ValueError):
            _task_agent_ui_contracts([{'source_refs': {'agent_ui': {'mode': 'runtime'}}}])

    def test_ordinary_agent_business_form_does_not_require_conversation_host(self) -> None:
        """后端 Agent 的 pageIds 不能把普通 JSON 表单误当共享会话入口。"""
        from app.agents.frontend.runtime_instruction import runtime_frontend_instruction
        _, product, technical = fixture()
        technical['api_contracts'] = [{'id': 'report', 'endpoints': [{'id': 'report.create',
            'path': '/api/report', 'agentCall': {'responseMode': 'json'}}]}]
        instruction = runtime_frontend_instruction(technical,
            [{'unit_id': 'page:' + product['pages'][0]['pageId'], 'source_refs': {}}])
        self.assertIn('responseMode=json', instruction)
        self.assertNotIn('AgentConversation', instruction)
        self.assertNotIn('/agents/assistant/run', instruction)

    def test_source_check_changes_when_shared_contract_changes(self) -> None:
        """验收编译保留共享来源摘要，不能把上次通过结果用于另一个包/设计版本。"""
        from app.services.business_acceptance import compile_business_acceptance
        from tests.test_business_acceptance import _formal_context, _task
        task = _task('frontend.page', path='frontend/src/pages/orders/index.tsx', owner='frontend',
                     unit_id='page:orders', target_id='orders')
        task['source_refs']['agent_ui'] = {'mode': 'shared_host', 'pageId': 'orders', 'sha256': 'a' * 64}
        before = compile_business_acceptance([task], _formal_context())[0]
        task['source_refs']['agent_ui']['sha256'] = 'b' * 64
        after = compile_business_acceptance([task], _formal_context())[0]
        def source(value: dict) -> dict:
            """只读取共享验收项的独立来源，不依赖无关业务检查的顺序。"""
            check = next(item for item in value['business_acceptance_checks']
                         if item['kind'] == 'frontend.agent_ui_runtime_contract')
            return next(item for item in check['sources'] if item['artifact'] == 'agent_ui_build_contract')
        self.assertNotEqual(source(before)['sha256'], source(after)['sha256'])
