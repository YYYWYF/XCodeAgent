"""连接条件只投影当前正式事实，不以静态支持冒充真实服务验收。"""

from copy import deepcopy
from unittest import TestCase

from app.services.agent_ui_connection_requirements import connection_requirements
from tests.test_runtime_endpoint_contracts import agent


class ConnectionRequirementsTests(TestCase):
    """覆盖纯文本、身份配置和未闭合公共能力的分项边界。"""

    def setUp(self) -> None:
        """准备无业务写入和上下文要求的正式会话。"""
        self.agent = agent('assistant', 'chat')
        self.config = {'auth': {'enable': False}, 'authorization': {'enabled': False}}

    def test_plain_text_is_supported_but_never_enables_sending(self) -> None:
        """路径和会话合同静态成立，不代表代理、运行身份或实际对话通过。"""
        before = deepcopy((self.config, self.agent))
        report = connection_requirements(self.config, self.agent)
        checks = {item['id']: item for item in report['checks']}
        self.assertEqual(report['endpoint'], '/agents/assistant/run')
        self.assertEqual(report['healthPath'], '/__runtime/health')
        self.assertEqual(report['principalSource'], 'runtime_anonymous_session')
        self.assertEqual(report['verificationScope'], 'static_contract')
        self.assertIs(report['sendEnabled'], False)
        self.assertEqual(checks['plain_text']['status'], 'supported')
        self.assertEqual(checks['runtime_identity']['status'], 'supported')
        self.assertEqual(checks['same_origin_session']['status'], 'not_verified')
        self.assertEqual(checks['run_identity']['status'], 'supported')
        self.assertEqual(checks['send_admission']['status'], 'supported')
        self.assertEqual(checks['context']['status'], 'not_required')
        self.assertEqual(checks['interrupts']['status'], 'not_required')
        self.assertEqual((self.config, self.agent), before)

    def test_authentication_and_authorization_use_only_application_config(self) -> None:
        """任一开关开启、缺失或类型错误都不能当作匿名会话已获准。"""
        for config in ({}, {'auth': {'enable': False}},
                {'auth': {'enable': True}, 'authorization': {'enabled': False}},
                {'auth': {'enable': False}, 'authorization': {'enabled': True}},
                {'auth': {'enable': 0}, 'authorization': {'enabled': False}},
                {'auth': {'enable': False}, 'authorization': None}):
            with self.subTest(config=config):
                report = connection_requirements(config, self.agent)
                checks = {item['id']: item for item in report['checks']}
                self.assertEqual(checks['runtime_identity']['status'], 'blocked')
                self.assertIs(report['sendEnabled'], False)

    def test_declared_business_capabilities_cannot_be_downgraded_to_plain_text(self) -> None:
        """任务、结构化输入/结果和真实 Tool 需要对应验收，不能静默忽略。"""
        for change in ('task', 'input', 'result', 'tools'):
            contract = deepcopy(self.agent)
            capability = contract['interactionContract']
            if change == 'task':
                capability['mode'] = 'task'
            elif change == 'input':
                capability['inputSchema']['properties']['recordId'] = {'type': 'string'}
            elif change == 'result':
                capability['resultSchema'] = {'type': 'string'}
            else:
                contract['agentSettings']['tools'] = {'enabled': True, 'bindings': [{'toolId': 'lookup'}]}
            with self.subTest(change=change):
                report = connection_requirements(self.config, contract)
                checks = {item['id']: item for item in report['checks']}
                self.assertEqual(checks['plain_text']['status'], 'not_required')
                self.assertIs(report['sendEnabled'], False)

    def test_context_and_interactions_use_standard_protocol(self) -> None:
        """正式声明上下文或审批时保留缺口，绝不把澄清当作批准。"""
        self.agent['agentSettings']['context'] = {'pageId': 'chat'}
        self.agent['interactionContract']['interactionKinds'] = ['confirmation']
        report = connection_requirements(self.config, self.agent)
        checks = {item['id']: item for item in report['checks']}
        self.assertEqual(checks['context']['status'], 'supported')
        self.assertEqual(checks['interrupts']['status'], 'supported')

    def test_report_excludes_business_payload_and_credentials(self) -> None:
        """只输出固定原因和运行路径，不把配置、Prompt 或输入内容回传到计划。"""
        self.config['private'] = 'sensitive-configuration'
        self.agent['agentSettings']['prompt']['systemPrompt'] = 'private-prompt'
        report = str(connection_requirements(self.config, self.agent))
        self.assertNotIn('sensitive-configuration', report)
        self.assertNotIn('private-prompt', report)

    def test_invalid_agent_identity_cannot_create_arbitrary_target_path(self) -> None:
        """路径必须由有效正式身份派生，不能注入 URL 或相对目录。"""
        for identity in ('../other', 'https://example.com', 'runtime'):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                connection_requirements(self.config, {**self.agent, 'agentId': identity})
