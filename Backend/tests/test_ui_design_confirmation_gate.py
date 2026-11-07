"""复现 Electron 中 0/N 设计稿被全部确认绕过的确认门问题。"""
import tempfile
import unittest

from app.graph.nodes.ui_confirmation import _product_plan_hash, _verified_ui_designs_for_confirmation


class UiDesignConfirmationGateTests(unittest.TestCase):
    """未生成页必须阻止确认，完整且有效的 TSX 仍可确认。"""

    def state(self, workspace):
        """构造单页无交互产品事实，避免依赖模型或历史产物。"""
        return {'workspace': workspace, 'product_plan': {'confirmation_status': 'confirmed', 'pages': [{
            'pageId': 'home', 'name': '首页', 'path': '/page/home',
            'information_items': [{'itemId': 'welcome', 'label': '欢迎文字'}], 'actions': [],
        }]}}

    def test_unfinished_pages_cannot_be_confirmed(self):
        """未生成、排队、生成中、失败、取消不能变成正式 UI 确认。"""
        with tempfile.TemporaryDirectory() as workspace:
            state = self.state(workspace)
            for status in ('pending', 'queued', 'generating', 'stopping', 'generation_failed', 'cancelled'):
                with self.subTest(status=status):
                    manifest = {'product_plan_sha256': _product_plan_hash(state), 'pages': [{
                        'pageId': 'home', 'page_key': 'Home', 'status': status,
                    }]}
                    verified, errors = _verified_ui_designs_for_confirmation(state, manifest)
                    self.assertTrue(any('尚未生成并通过校验' in error for error in errors))
                    self.assertEqual(verified['confirmation_status'], 'pending_user_confirmation')

    def test_generated_page_still_requires_valid_code(self):
        """仅有 confirmed 标签但没有真实代码也不能确认。"""
        with tempfile.TemporaryDirectory() as workspace:
            state = self.state(workspace)
            _, errors = _verified_ui_designs_for_confirmation(state, {
                'product_plan_sha256': _product_plan_hash(state),
                'pages': [{'pageId': 'home', 'page_key': 'Home', 'status': 'confirmed'}],
            })
            self.assertTrue(errors)

    def test_complete_valid_page_can_be_confirmed(self):
        """完整生成且产品事实绑定有效的页面不被新门禁误拦。"""
        with tempfile.TemporaryDirectory() as workspace:
            state = self.state(workspace)
            _, errors = _verified_ui_designs_for_confirmation(state, {
                'product_plan_sha256': _product_plan_hash(state), 'pages': [{
                    'pageId': 'home', 'page_key': 'Home', 'status': 'confirmed',
                    'code': '<div data-information-item-id="welcome" data-control-id="welcome-display">欢迎</div>',
                }],
            })
            self.assertEqual(errors, [])
