"""覆盖共享 Host 动作从产品、设计到技术候选编译的实际边界。"""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app.services.page_implementation_contract import (
    attach_page_implementation_contracts, build_page_implementation_contracts,
    validate_page_implementation_contracts,
)
from app.services.project_plan import create_technical_plan
from app.services.runtime_agent_page_references import compile_agent_page_references
from app.services.product_plan import requirement_spec_sha256
from app.services.ui_design_agent_surfaces import project_agent_design_pages
from app.services.ui_design_interactions import agent_project_page_bindings, require_ui_interaction_effects
from tests.test_technical_planning_agent_context import _planning_inputs


class AgentHostPlanningTests(TestCase):
    """确认平台动作不会重复变成业务接口，普通页面依赖继续保留。"""

    def setUp(self) -> None:
        """构造共享 Host 同时承担开关和发送动作的已确认产品。"""
        self.spec, self.product, self.candidate = _planning_inputs()
        self.page = self.product['pages'][0]
        binding = self.product['agents'][0]['pageActionBindings'][0]
        for action_id, effect in (('open_panel', '打开当前共享助手'), ('close_panel', '关闭当前共享助手')):
            self.page['actions'].append({'actionId': action_id,
                'behavior': {'type': 'interface', 'expectedResult': effect}})
            binding['actionIds'].append(action_id)
        self.page_id = self.page['pageId']
        self.send_id = binding['actionIds'][0]

    def wrong_references(self) -> dict:
        """复现将 Agent ID 误作接口 ID 并加入页面依赖的模型候选。"""
        return {'action_implementations': [{'actionId': self.send_id, 'endpointId': 'agent_general_chat'}],
                'endpoint_dependencies': [{'endpoint_id': 'agent_general_chat'}]}

    def test_generation_compiles_agent_references_and_confirmed_host_effects(self) -> None:
        """走真实技术规划生成入口，已确认 Host 开关不再要求模型填写效果。"""
        self.candidate['pages'][0]['references'] = self.wrong_references()
        before = deepcopy(self.candidate)
        plan = create_technical_plan(self.spec, agent_plan=self.candidate)
        ui = {'confirmation_status': 'confirmed', 'pages': [
            {'pageId': page['pageId'], 'bindings': {'actions': [
                {'actionId': action['actionId'], 'controlIds': []} for action in page['actions']]}}
            for page in self.product['pages']]}
        require_ui_interaction_effects(self.product, ui)
        plan = attach_page_implementation_contracts(plan, self.product, ui)
        self.assertEqual(validate_page_implementation_contracts(plan, self.product, ui), [])
        page = next(item for item in plan['pages'] if item['pageId'] == self.page_id)
        self.assertEqual(page['references']['action_implementations'], [])
        self.assertEqual(page['references']['endpoint_dependencies'], [])
        compiled = next(item for item in build_page_implementation_contracts(plan, self.product, ui)
                        if item['pageId'] == self.page_id)
        actions = {item['actionId']: item for item in compiled['actionBindings']}
        self.assertEqual(actions[self.send_id]['bindingType'], 'agent')
        self.assertEqual(actions['open_panel']['localEffect'], '打开当前共享助手')
        self.assertEqual(actions['close_panel']['localEffect'], '关闭当前共享助手')
        self.assertEqual(self.candidate, before)

    def test_manifest_projects_host_effect_without_inventing_control_ids(self) -> None:
        """Host 动作从已确认产品投影效果，页面正文仍读取真实源码。"""
        # 设计入口消费正式需求，不携带技术规划专用的产品上下文投影。
        spec = {key: value for key, value in self.spec.items() if key != 'confirmed_product_plan'}
        spec = {**spec, 'confirmation_status': 'confirmed'}
        product = {**self.product, 'requirement_spec_sha256': requirement_spec_sha256(spec)}
        pages = project_agent_design_pages(spec, product)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'src/pages/page-0.tsx'
            source.parent.mkdir(parents=True)
            source.write_text('export default function Page() { return <main /> }')
            # 该用例只验证共享 Host 动作，正文信息在其他交互回归中覆盖。
            page = {**pages[0], 'information_items': []}
            bindings = agent_project_page_bindings(root, page, [item['pageId'] for item in pages])
        actions = {item['actionId']: item for item in bindings['actions']}
        self.assertEqual(actions['open_panel']['uiEffect'], '打开当前共享助手')
        self.assertEqual(actions['open_panel']['controlIds'], [])
        self.assertNotIn('uiEffect', actions[self.send_id])

    def test_valid_endpoint_shared_with_agent_proposal_is_preserved(self) -> None:
        """模型误选真实接口时只移除 Agent 动作的选择，真实页面依赖保留。"""
        candidate = deepcopy(self.candidate)
        candidate['api_contracts'] = [{'id': 'inventory', 'endpoints': [{'id': 'lookup'}]}]
        candidate['pages'][0]['references'] = {
            'action_implementations': [{'actionId': self.send_id, 'endpointId': 'lookup'}],
            'endpoint_dependencies': [{'api_contract_id': 'inventory', 'endpoint_id': 'lookup'}]}
        result = compile_agent_page_references(candidate, self.product)
        self.assertEqual(result['pages'][0]['references']['endpoint_dependencies'],
                         candidate['pages'][0]['references']['endpoint_dependencies'])
        self.assertEqual(result['api_contracts'], candidate['api_contracts'])

    def test_missing_business_and_sequence_dependencies_are_not_silenced(self) -> None:
        """业务动作或组合业务步骤仍引用缺失接口时保留原错误输入。"""
        for implementation in (
            {'actionId': 'save', 'endpointId': 'agent_general_chat'},
            {'actionId': 'save', 'stepBindings': [{'stepId': 'write', 'endpointId': 'agent_general_chat'}]},
        ):
            with self.subTest(implementation=implementation):
                candidate = deepcopy(self.candidate)
                refs = self.wrong_references()
                refs['action_implementations'].append(implementation)
                candidate['pages'][0]['references'] = refs
                result = compile_agent_page_references(candidate, self.product)
                self.assertEqual(result['pages'][0]['references']['action_implementations'], [implementation])
                self.assertEqual(result['pages'][0]['references']['endpoint_dependencies'], refs['endpoint_dependencies'])

    def test_ordinary_interface_still_requires_real_ui_effect(self) -> None:
        """平台归属外的页面按钮不能用产品预期冒充已设计效果。"""
        self.page['actions'].append({'actionId': 'filter',
            'behavior': {'type': 'interface', 'expectedResult': '展开筛选'}})
        with self.assertRaisesRegex(ValueError, 'filter'):
            require_ui_interaction_effects(self.product, {'confirmation_status': 'confirmed', 'pages': []})

    def test_compilation_is_idempotent_and_keeps_unrelated_invalid_dependencies(self) -> None:
        """重复编译不改变结果，也不按接口名称猜测清除无关错误。"""
        candidate = deepcopy(self.candidate)
        refs = self.wrong_references()
        refs['endpoint_dependencies'].append({'endpoint_id': 'missing_business'})
        candidate['pages'][0]['references'] = refs
        result = compile_agent_page_references(candidate, self.product)
        self.assertEqual(result['pages'][0]['references']['endpoint_dependencies'], [{'endpoint_id': 'missing_business'}])
        self.assertEqual(compile_agent_page_references(result, self.product), result)

    def test_repair_candidate_assembly_uses_the_same_agent_reference_compiler(self) -> None:
        """恢复失败候选时也重编译 Agent 引用，重新生成入口不会留下原误绑定。"""
        from app.graph.nodes.planning import _attach_technical_plan_contracts
        plan = create_technical_plan(self.spec, agent_plan=self.candidate)
        plan['pages'][0]['references'] = self.wrong_references()
        state = {'workflow_scope': 'application_planning', 'workspace': '/workspace',
                 'product_plan': self.product, 'ui_designs': {'confirmation_status': 'confirmed', 'pages': []}}
        with patch('app.graph.nodes.planning.read_application_config', return_value={}), \
                patch('app.graph.nodes.planning.bind_plan_entity_tables', side_effect=lambda value, config: value):
            result = _attach_technical_plan_contracts(state, plan)
        self.assertEqual(result['pages'][0]['references']['action_implementations'], [])
        self.assertEqual(result['pages'][0]['references']['endpoint_dependencies'], [])
