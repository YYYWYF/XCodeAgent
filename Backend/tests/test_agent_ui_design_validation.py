"""验证包消费页面与普通页面采用一致的业务标记校验。"""

import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from app.services.agent_ui_design_generator import _host_files, _prompt, _validate_files
from app.services.ui_design_manifest import validate_ui_design_code


class AgentUiDesignValidationTests(unittest.TestCase):
    """通过真实 TypeScript AST 检查器覆盖原先额外拒绝的页面写法。"""

    def setUp(self) -> None:
        """复用已安装工具中的 TypeScript，并隔离候选文件与日志。"""
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        (root / 'logs').mkdir()
        repository = Path(__file__).resolve().parents[2]
        self.project = SimpleNamespace(root=root, workspace=root, project_dir=repository / 'Frontend')
        self.node = Path(shutil.which('node'))
        self.page = {'pageId': 'chat', 'name': '会话', 'actions': [
            {'actionId': 'back_home', 'behavior': {'type': 'navigation', 'targetPageId': 'home'}}],
            'information_items': []}

    def validate(self, code: str, page: dict | None = None) -> dict:
        """使用正式生成入口验证一份候选，不调用模型或写入用户项目。"""
        return _validate_files(self.project, {'src/pages/page-0.tsx': code},
                               [page or self.page], self.node, '')

    def test_declared_navigation_accepts_same_local_placeholder_as_ordinary_page(self) -> None:
        """普通页面允许的导航占位行为也能用于包消费设计。"""
        source = '''export default function Page() {
          return <button data-action-id="back_home" data-control-id="back" onClick={() => {}}>首页</button>
        }'''
        self.assertEqual(validate_ui_design_code(self.page, source), [])
        self.assertEqual(self.validate(source)['chat']['navigate'], [])

    def test_array_access_and_jsx_spread_do_not_add_design_restrictions(self) -> None:
        """合法本地数据取值和属性展开与普通页面采用相同业务标记判断。"""
        source = '''export default function Page() {
          const labels = ['首页']; const props = { disabled: false };
          return <button {...props} data-action-id="back_home" data-control-id="back">{labels[0]}</button>
        }'''
        self.assertEqual(validate_ui_design_code(self.page, source), [])
        self.validate(source)

    def test_wrapped_panel_callback_on_semantic_control_keeps_inline_launcher(self) -> None:
        """共享入口允许箭头函数包装及语义控件，不额外限定 button 标签。"""
        page = {**self.page, 'actions': [{'actionId': 'open_panel', 'behavior': {'type': 'interface'}}]}
        source = '''export default function Page({onOpenAgentPanel}) {
          return <div role="button" data-action-id="open_panel" data-control-id="open" data-ui-effect="打开"
            onClick={() => onOpenAgentPanel()}>打开</div>
        }'''
        self.assertEqual(validate_ui_design_code(page, source), [])
        controls = self.validate(source, page)
        self.assertEqual(controls['chat']['open'], ['open_panel'])
        host_page = {**page, 'agent_surfaces': [
            {'type': 'floating_panel', 'actionIds': [], 'name': '助手', 'purpose': '会话'}]}
        self.assertIn('"hasInlineLauncher":true', _host_files([host_page], controls)['src/design/pages.tsx'])

    def test_explicit_wrong_navigation_target_is_reported(self) -> None:
        """静态声明的真实 Host 导航仍必须指向正式动作的目标页面。"""
        source = '''export default function Page({onNavigatePage}) {
          return <button data-action-id="back_home" data-control-id="back"
            onClick={() => onNavigatePage('other')}>首页</button>
        }'''
        with self.assertRaisesRegex(ValueError, '实际导航目标'):
            self.validate(source)

    def test_unknown_action_is_rejected_by_both_generators(self) -> None:
        """对齐普通页面仍拒绝自行新增的业务动作，并返回实际失败页面。"""
        source = '''export default function Page() {
          return <main><button data-action-id="back_home" data-control-id="back">首页</button>
            <button data-action-id="extra" data-control-id="extra">其他</button></main>
        }'''
        self.assertTrue(validate_ui_design_code(self.page, source))
        with self.assertRaisesRegex(ValueError, '业务页面 chat.*越界'):
            self.validate(source)

    def test_source_boundary_error_includes_actual_diagnostic(self) -> None:
        """边界失败回传具体源码问题，保留禁止业务页直接发送请求的规则。"""
        with self.assertRaisesRegex(ValueError, 'forbidden capability fetch'):
            self.validate('export default function Page() { fetch("/agent"); return <main /> }')

    def test_prompt_reuses_ordinary_page_literal_marker_examples(self) -> None:
        """模型获得与普通页面相同的逐动作示例和未声明导航说明。"""
        prompt = _prompt([self.page], ('src/pages/page-0.tsx',))
        self.assertIn('REQUIRED product action marker', prompt)
        self.assertIn('data-action-id="back_home"', json.loads(
            prompt.split('Exact required business contract for EACH output file:\n')[1]
                  .split('\nCurrent confirmed pages')[0])[0]['pageBrief'])
        self.assertIn('Return/back navigation is allowed only when', prompt)
