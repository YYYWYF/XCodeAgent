"""设计稿的生成状态、页面内容和上游摘要均不阻断显式确认。"""
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app.graph.nodes.ui_confirmation import ui_confirmation


class UiDesignConfirmationGateTests(unittest.IsolatedAsyncioTestCase):
    """通过真实确认分支验证设计稿只作可选参考。"""

    async def test_any_ui_manifest_can_be_confirmed_without_readiness_checks(self):
        """空清单、过期摘要、无代码、错误控件和各类生成状态都可继续。"""
        with tempfile.TemporaryDirectory() as workspace:
            for status in ('pending', 'queued', 'generating', 'stopping', 'generation_failed', 'cancelled', 'confirmed'):
                for pages in ([], [{'pageId': 'old-page', 'status': status}]):
                    with self.subTest(status=status, pages=pages):
                        manifest = {'confirmation_status': 'pending_user_confirmation',
                                    'product_plan_sha256': 'outdated', 'pages': pages}
                        state = {'workspace': workspace, 'requirement_spec': {},
                                 'product_plan': {'confirmation_status': 'confirmed', 'pages': []},
                                 'ui_designs': manifest, 'workflow_scope': 'application_planning',
                                 'application_planning_interaction': {'action': 'confirm'}}
                        with patch('app.graph.nodes.ui_confirmation.require_current_product_plan', return_value=state['product_plan']), \
                             patch('app.graph.nodes.ui_confirmation._latest_ui_designs', new=AsyncMock(return_value=manifest)), \
                             patch('app.graph.nodes.ui_confirmation._persist_ui_designs') as persist, \
                             patch('app.graph.nodes.ui_confirmation._ui_design_confirmed_payload', return_value={}):
                            result = await ui_confirmation(state)
                        self.assertEqual(result['status'], 'completed')
                        self.assertEqual(result['ui_designs']['confirmation_status'], 'confirmed')
                        self.assertEqual(result['ui_designs']['pages'], pages)
                        persist.assert_called_once()

    async def test_missing_manifest_and_explicit_skip_can_continue(self):
        """没有设计稿也能确认；显式跳过不进入生成器或代码验证。"""
        with tempfile.TemporaryDirectory() as workspace:
            for action in ('confirm', 'ui_action'):
                state = {'workspace': workspace, 'requirement_spec': {},
                         'product_plan': {'confirmation_status': 'confirmed', 'pages': []},
                         'ui_designs': {}, 'workflow_scope': 'application_planning',
                         'application_planning_interaction': {'action': action},
                         'ui_design_action': {'action': 'skip'} if action == 'ui_action' else None}
                with patch('app.graph.nodes.ui_confirmation.require_current_product_plan', return_value=state['product_plan']), \
                     patch('app.graph.nodes.ui_confirmation._latest_ui_designs', new=AsyncMock(return_value={})), \
                     patch('app.graph.nodes.ui_confirmation._persist_ui_designs'), \
                     patch('app.graph.nodes.ui_confirmation._ui_design_confirmed_payload', return_value={}):
                    result = await ui_confirmation(state)
                self.assertEqual(result['status'], 'completed')
