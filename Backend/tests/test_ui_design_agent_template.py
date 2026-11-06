"""旧模板不能再生成或证明当前共享设计。"""

from unittest import TestCase
from app.services.ui_design_manifest import build_ui_page_manifest, validate_ui_design_code
from app.services.ui_design_agent_surfaces import inspect_agent_surface_bindings, manifest_agent_surface_bindings


class UiDesignAgentTemplateTests(TestCase):
    """拒绝旧虚拟模块、固定组件与旧配置证据，保留普通页。"""

    def test_fixed_source_is_rejected_even_on_a_normal_page(self) -> None:
        """没有 Surface 也不能把旧会话模板藏在业务稿中。"""
        for code in (
            "import { AgentConversationTemplate } from '@xcodeagent/agent-ui-design'",
            "const page = <AgentFloatingPanelTemplate configJson='{}' />",
        ):
            self.assertTrue(inspect_agent_surface_bindings([], code)['invalid_markers'])
            self.assertTrue(validate_ui_design_code({'pageId': 'home'}, code))

    def test_product_surface_does_not_create_template_evidence(self) -> None:
        """产品事实只投影稳定绑定，确认凭据另由真实多文件资产派生。"""
        page = {'pageId': 'chat', 'agent_surfaces': [{'agentId': 'assistant',
            'type': 'standalone_page', 'actionIds': ['ask'], 'contextItemIds': []}]}
        bindings = manifest_agent_surface_bindings(page, {'surfaces': [{'template': {'version': 'agent-ui.v1'}}]})
        self.assertNotIn('template', bindings[0])
        result = build_ui_page_manifest(page, page_key='Chat', code='export default () => null')
        self.assertEqual(result['verification']['status'], 'failed')
        self.assertTrue(any('多文件' in error for error in result['verification']['errors']))

    def test_ordinary_page_keeps_single_file_validation(self) -> None:
        """普通业务稿不要求共享 Agent 资产。"""
        self.assertEqual(validate_ui_design_code({'pageId': 'home'}, 'export default () => <main>业务</main>'), [])
