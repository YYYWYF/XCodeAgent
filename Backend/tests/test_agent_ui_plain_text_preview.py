"""纯文本真实预览只接受当前明确允许的无业务能力，不修改正式合同。"""

from copy import deepcopy
from unittest import TestCase

from app.services.agent_ui_plain_text_preview import plain_text_preview_allowed
from tests.test_runtime_endpoint_contracts import agent


class PlainTextPreviewTests(TestCase):
    """覆盖匿名、纯文本、上下文和业务能力的隔离。"""

    def setUp(self) -> None:
        """准备与真实简单聊天相同的正式能力。"""
        self.application = {'auth': {'enable': False}, 'authorization': {'enabled': False}}
        self.contract = agent('assistant', 'chat')

    def test_plain_conversation_and_description_only_context_are_allowed(self) -> None:
        """描述性上下文不声明页面数据；两轮可信历史仍由原 Python 会话管理。"""
        self.contract['agentSettings']['context'] = {'description': '只根据用户消息回答。'}
        before = deepcopy(self.contract)
        self.assertTrue(plain_text_preview_allowed(self.application, self.contract))
        self.assertEqual(self.contract, before)

    def test_business_features_never_become_plain_text(self) -> None:
        """任务、工具、资源、业务输入/结果和审批均不能降级为简单聊天。"""
        for feature in ('task', 'tools', 'skills', 'knowledge', 'input', 'result', 'state', 'interrupt', 'private', 'page'):
            contract = deepcopy(self.contract)
            if feature == 'task':
                contract['interactionContract']['mode'] = 'task'
            elif feature in {'tools', 'skills', 'knowledge'}:
                contract['agentSettings'][feature]['enabled'] = True
            elif feature == 'input':
                contract['interactionContract']['inputSchema']['properties']['recordId'] = {'type': 'string'}
            elif feature == 'result':
                contract['interactionContract']['resultSchema'] = {'type': 'string'}
            elif feature == 'state':
                contract['interactionContract']['publicStateSchema'] = {}
            elif feature == 'interrupt':
                contract['interactionContract']['interactionKinds'] = ['approval_required']
            elif feature == 'private':
                contract['publicEndpoint'] = False
            else:
                contract['agentSettings']['context'] = {'pageId': 'chat'}
            with self.subTest(feature=feature):
                self.assertFalse(plain_text_preview_allowed(self.application, contract))

    def test_authentication_must_be_explicitly_anonymous(self) -> None:
        """不以展示用户或缺省配置替代真实身份模式。"""
        for config in ({}, {'auth': {'enable': False}},
                       {'auth': {'enable': True}, 'authorization': {'enabled': False}}):
            with self.subTest(config=config):
                self.assertFalse(plain_text_preview_allowed(config, self.contract))
