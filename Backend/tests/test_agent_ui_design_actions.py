"""原生会话操作须由实际组合承接，不能登记一个页面按钮就宣称实现。"""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from app.services.agent_ui_design_actions import validate_native_action_composition
from app.services.agent_ui_design_generator import _host_files, _validate_files
from app.services.ui_design_agent_surfaces import project_ui_design_pages
from tests.test_agent_action_flow_contract import action, product


class AgentUiNativeActionTests(TestCase):
    """验证实际默认组合以及缺失、错放和脱离会话的原生操作。"""

    def setUp(self) -> None:
        """准备明确归属但不依赖操作名称的正式页面输入。"""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'src/agent-ui/app-ui.json'
        self.source.parent.mkdir(parents=True)
        repository = Path(__file__).resolve().parents[2]
        self.model = json.loads((repository / 'tools/agent-ui-consumer/src/agent-ui/app-ui.json').read_text())
        value = product()
        value['agents'] = value['agents'][:1]
        value['agents'][0]['pageActionBindings'][0]['actionIds'] = ['ask']
        value['pages'][0]['actions'] = [action('ask', 'order_assistant'),
            action('halt', 'order_assistant', 'stop_generation'),
            action('duplicate', 'order_assistant', 'copy_response')]
        self.pages = project_ui_design_pages(value)

    def write_model(self, model: dict) -> None:
        """将当前案例的真实组合写入隔离工程。"""
        self.source.write_text(json.dumps(model))

    def test_current_official_composition_supports_both_operations(self) -> None:
        """官方输入区及回复操作在各自上下文中，页面无需再造业务正文。"""
        self.write_model(self.model)
        validate_native_action_composition(self.root, self.pages)

    def test_host_has_no_extra_body_or_native_action_launchers(self) -> None:
        """没有独立业务操作的会话页直接渲染 Agent，停止和复制不变成入口按钮。"""
        files = _host_files(self.pages)
        self.assertNotIn('import Page0', files['src/design/pages.tsx'])
        surfaces = json.loads(files['src/design/surfaces.ts'].split(' = ', 1)[1].removesuffix(' as const\n'))
        self.assertEqual([item['actionId'] for item in surfaces[0]['entries']], ['ask'])

    def test_page_local_native_success_button_is_rejected(self) -> None:
        """普通正文不能通过本地状态和原生 actionId 冒充已经实现的复制操作。"""
        page = deepcopy(self.pages[0])
        page['actions'].append({'actionId': 'back', 'behavior': {'type': 'navigation', 'targetPageId': 'home'}})
        code = '''import { useState } from 'react'
/** 复现错误的页面复制按钮，不接入官方回复。 */
export default function Page({onNavigatePage}: {onNavigatePage: (pageId: string) => void}) {
  const [copied, setCopied] = useState(false)
  return <main>
    <button data-action-id="back" data-control-id="back" onClick={() => onNavigatePage('home')}>返回</button>
    <button data-action-id="duplicate" data-control-id="copy" onClick={() => setCopied(true)}>{copied ? '已复制' : '复制'}</button>
  </main>
}'''
        (self.root / 'logs').mkdir()
        repository = Path(__file__).resolve().parents[2]
        project = SimpleNamespace(root=self.root, workspace=self.root, project_dir=repository / 'tools/agent-ui-consumer')
        with self.assertRaisesRegex(ValueError, '越界'):
            _validate_files(project, {'src/pages/page-0.tsx': code}, [page], Path('node'), '')

    def test_missing_or_misplaced_copy_does_not_count_as_implemented(self) -> None:
        """只有已挂载回复上下文中的复制控件能承接复制语义。"""
        for replacement in ([], [{'pluginId': 'reload-action'}]):
            model = deepcopy(self.model)
            surface = model['root']['children'][1]['child']['children'][0]['plugins'][0]
            surface['slots']['assistantResponseFooter'][0]['slots']['actions'] = replacement
            model['applicationPlugins'].append({'pluginId': 'copy-action'})
            self.write_model(model)
            with self.assertRaisesRegex(ValueError, 'duplicate.*copy_response'):
                validate_native_action_composition(self.root, self.pages)

    def test_stop_requires_a_submit_action_in_the_conversation_composer(self) -> None:
        """同名插件放在回复页脚不能停止当前会话。"""
        surface = self.model['root']['children'][1]['child']['children'][0]['plugins'][0]
        surface['slots']['composer'][0]['slots']['submitAction'] = []
        surface['slots']['assistantResponseFooter'][0]['slots']['actions'].append({'pluginId': 'submit-action'})
        self.write_model(self.model)
        with self.assertRaisesRegex(ValueError, 'halt.*stop_generation'):
            validate_native_action_composition(self.root, self.pages)

    def test_non_native_business_copy_does_not_require_native_plugins(self) -> None:
        """普通复制业务字段按正式行为保留，不从标题或 actionId 猜归属。"""
        self.pages[0]['actions'] = [{'actionId': 'stop_generation', 'name': '复制回复',
            'behavior': {'type': 'business', 'expectedResult': '复制业务编号'}}]
        validate_native_action_composition(self.root, self.pages)

    def test_missing_composition_cannot_be_published(self) -> None:
        """缺失组合文件时报告能力缺口，不能把本地成功提示当作证据。"""
        with self.assertRaisesRegex(ValueError, '组合'):
            validate_native_action_composition(self.root, self.pages)

    def test_disabled_plugin_or_ancestor_cannot_supply_native_operations(self) -> None:
        """已禁用的官方插件及其子树都不算可用能力。"""
        for target in ('surface', 'copy'):
            model = deepcopy(self.model)
            surface = model['root']['children'][1]['child']['children'][0]['plugins'][0]
            plugin = surface if target == 'surface' else surface['slots']['assistantResponseFooter'][0]['slots']['actions'][0]
            plugin['enabled'] = False
            self.write_model(model)
            with self.assertRaisesRegex(ValueError, '原生操作'):
                validate_native_action_composition(self.root, self.pages)

    def test_sidebar_content_and_active_stack_follow_current_public_layout(self) -> None:
        """侧栏正文和已选层继续可用，未选中的层不能提供当前页面操作。"""
        self.model['root'] = {'type': 'sidebar', 'defaultActive': None, 'items': [],
            'content': {'type': 'stack', 'activeIndex': 1,
                'children': [{'type': 'slot', 'plugins': []}, self.model['root']]}}
        self.write_model(self.model)
        validate_native_action_composition(self.root, self.pages)
        self.model['root']['content']['activeIndex'] = 0
        self.write_model(self.model)
        with self.assertRaisesRegex(ValueError, '原生操作'):
            validate_native_action_composition(self.root, self.pages)
