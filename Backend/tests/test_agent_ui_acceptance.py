"""T29 分层证据不由运行终态自证，且只失效相关输入。"""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app.services.agent_ui_acceptance_cases import acceptance_cases
from app.services.agent_ui_acceptance import acceptance_matrix, record_acceptance
from tests.test_runtime_endpoint_contracts import agent


class AcceptanceEvidenceTests(TestCase):
    """隔离磁盘与受管观察身份，不启动模型或修改用户应用。"""

    def setUp(self) -> None:
        """准备平台派生上下文，调用方不能指定用例及摘要拥有源。"""
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        contract = agent('assistant', 'orders')
        self.context = {'agent': contract, 'pageId': 'orders',
                        'inputs': {'frontend': 'a', 'runtime': 'b', 'formal': 'c', 'presentation': 'p', 'config': 'd', 'packages': 'e', 'lock': 'f'}}
        self.context_patch = patch('app.services.agent_ui_acceptance.acceptance_contexts', return_value=[self.context])
        self.context_patch.start()
        self.addCleanup(self.context_patch.stop)
        self.observation = {'source': 'formal-text', 'identityMatches': True,
                            'sessionId': 'session', 'launchId': 'launch'}
        self.live = patch('app.services.agent_ui_acceptance.live_observation', return_value=self.observation)
        self.live.start()
        self.addCleanup(self.live.stop)

    def case(self, name: str) -> dict:
        """只从实际矩阵选择当前用例。"""
        return next(item for item in acceptance_matrix(self.root)['cases'] if item['scenario'] == name)

    def record(self, name: str, **changes: object) -> dict:
        """模拟用户逐项核验后显式提交，不把布尔值标为机器证明。"""
        case = self.case(name)
        value = {'caseId': case['caseId'], 'fingerprint': case['fingerprint'], 'source': 'real',
                 'outcome': 'passed', 'checks': [item['id'] for item in case['checks']]}
        value.update(changes)
        return record_acceptance(self.root, value, thread_id='review', run_id='report')

    def test_plain_text_does_not_pass_unused_business_capabilities(self) -> None:
        """未声明审批和结构化结果时明确不适用，基础异常场景仍需核验。"""
        cases = acceptance_cases(self.context['agent'])
        by_name = {item['scenario']: item for item in cases}
        self.assertFalse(by_name['approve']['applicable'])
        self.assertFalse(by_name['business_result']['applicable'])
        for name in ('conversation', 'error', 'stop', 'unknown'):
            self.assertTrue(by_name[name]['applicable'])
        self.assertEqual(self.case('approve')['status'], 'not_applicable')

    def test_task_and_input_capabilities_have_only_their_declared_cases(self) -> None:
        """任务型不强加多轮文本，结构化输入有独立核验项。"""
        capability = self.context['agent']['interactionContract']
        capability.update(mode='task', textOutput=False, resultSchema={'type': 'string'})
        capability['inputSchema']['properties']['note'] = {'type': 'string'}
        cases = {item['scenario']: item for item in acceptance_cases(self.context['agent'])}
        self.assertFalse(cases['conversation']['applicable'])
        self.assertTrue(cases['task_run']['applicable'])
        self.assertTrue(cases['business_input']['applicable'])

    def test_mock_or_empty_checks_cannot_pass_real_connection(self) -> None:
        """HTTP/终态和 Mock 不能替代真实模型、多轮和身份等完整核验。"""
        for changes in ({'source': 'official-mock'}, {'checks': []}, {'checks': ['RUN_FINISHED']}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.record('conversation', **changes)
        self.assertEqual(self.case('conversation')['status'], 'pending')

    def test_report_preserves_user_attestation_and_does_not_confirm_acceptance(self) -> None:
        """单项人工核验有来源与时间，但不会自动批准整个版本。"""
        matrix = self.record('conversation')
        case = next(item for item in matrix['cases'] if item['scenario'] == 'conversation')
        self.assertEqual(case['status'], 'passed')
        self.assertEqual(case['evidence']['method'], 'user_observed')
        self.assertEqual(case['evidence']['launchId'], 'launch')
        self.assertFalse(matrix['ready'])

    def test_runtime_change_preserves_visual_but_invalidates_connection(self) -> None:
        """页面视觉不依赖 Python 字节，运行连接不能复用旧 Python 证据。"""
        self.record('visual', source='real')
        self.record('conversation')
        self.context['inputs']['runtime'] = 'new-runtime'
        self.assertEqual(self.case('visual')['status'], 'passed')
        self.assertEqual(self.case('conversation')['status'], 'stale')

    def test_old_review_and_not_applicable_report_are_rejected(self) -> None:
        """旧摘要和未声明的业务能力均不能提交伪通过。"""
        before = self.case('conversation')['fingerprint']
        self.context['inputs']['frontend'] = 'edited'
        with self.assertRaises(ValueError):
            self.record('conversation', fingerprint=before)
        with self.assertRaises(ValueError):
            self.record('approve')

    def test_unknown_is_preserved_and_new_session_cannot_reuse_review(self) -> None:
        """未知不会升级成功，服务切换后旧审阅摘要不可继续登记。"""
        self.record('conversation', outcome='unknown', checks=[])
        before = self.case('conversation')['fingerprint']
        self.assertEqual(self.case('conversation')['status'], 'unknown')
        self.observation['launchId'] = 'new-launch'
        with self.assertRaises(ValueError):
            self.record('conversation', fingerprint=before)

    def test_invalid_live_identity_never_passes_real_evidence(self) -> None:
        """正式服务健康与本次源码/会话身份不闭合时不能登记真实通过。"""
        for changes in ({'source': 'replay'}, {'identityMatches': False}):
            previous = deepcopy(self.observation)
            self.observation.update(changes)
            with self.assertRaises(ValueError):
                self.record('conversation')
            self.observation.update(previous)

    def test_each_related_input_invalidates_evidence_and_other_page_does_not_inherit(self) -> None:
        """源码、正式合同、配置、包、锁变化分别失效；其他页面不继承通过记录。"""
        for key in ('frontend', 'runtime', 'formal', 'config', 'packages', 'lock'):
            self.record('conversation')
            self.context['inputs'][key] += 'changed'
            self.assertEqual(self.case('conversation')['status'], 'stale')
        self.context['pageId'] = 'other'
        self.assertEqual(self.case('conversation')['status'], 'pending')

    def test_missing_live_service_can_record_unknown_but_never_pass(self) -> None:
        """断网现场允许记录未知，缺健康和来源不能记录成功。"""
        self.observation.clear()
        self.record('conversation', outcome='unknown', checks=[])
        self.assertEqual(self.case('conversation')['status'], 'unknown')
        with self.assertRaises(ValueError):
            self.record('conversation')

    def test_prompt_change_does_not_discard_visual_evidence(self) -> None:
        """仅业务配置变化保留已核对的同一视觉合同。"""
        self.record('visual', source='real')
        self.context['inputs']['formal'] = 'new-prompt'
        self.assertEqual(self.case('visual')['status'], 'passed')

    def test_persisted_pass_cannot_omit_checks_or_change_source(self) -> None:
        """磁盘记录损坏不能因保留 passed 和摘要就绕过完整核验。"""
        self.record('conversation')
        path = self.root / '.devagentstudio/runtime/agent-ui-acceptance/evidence.json'
        original = json.loads(path.read_text())
        for change in ({'checks': []}, {'source': 'official-mock'}, {'checkedAt': 'invalid'}):
            damaged = deepcopy(original)
            next(iter(damaged.values())).update(change)
            path.write_text(json.dumps(damaged))
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.case('conversation')
        path.write_text(json.dumps(original))

    def test_visual_pass_must_match_current_observed_source(self) -> None:
        """实际运行来源与用户选择不一致时，视觉记录也不能冒充通过。"""
        with self.assertRaises(ValueError):
            self.record('visual', source='official-mock')


class AcceptanceDiskIdentityTests(TestCase):
    """实际遍历源码、包和正式输入，证明摘要不是由调用方自报。"""

    def test_actual_source_package_lock_and_application_changes_invalidate_review(self) -> None:
        """复用 T28 磁盘夹具，仅替代正式确认 owner，不替代文件字节摘要。"""
        from tests.test_agent_ui_observation import ObservationTests
        from tests.test_agent_integration_1008 import fixture
        from tests.test_agent_ui_build_contract import build_evidence
        from app.services.agent_ui_acceptance import acceptance_contexts
        from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints
        disk = ObservationTests()
        disk.setUp()
        self.addCleanup(disk.doCleanups)
        root = disk.root.resolve()
        requirement, product, technical = fixture()
        technical = materialize_runtime_endpoints(technical)
        requirement['confirmation_status'] = 'confirmed'
        application = {'auth': {'enable': False}, 'authorization': {'enabled': False}}
        page_id = product['pages'][0]['pageId']
        formal = root / '.devagentstudio/plans'
        formal.mkdir(parents=True)
        (formal / 'product-plan.json').write_text(json.dumps(product))
        spec_directory = root / '.devagentstudio/specs'
        spec_directory.mkdir()
        (spec_directory / 'requirement-spec.json').write_text(json.dumps(requirement))
        application_path = root / '.devagentstudio/application.json'
        application_path.write_text(json.dumps(application))
        evidence = build_evidence(page_id)
        contract = {'agentId': technical['agent_contracts'][0]['agentId'], 'presentation': {}, 'evidence': evidence}
        with patch('app.services.python_runtime_checks.runtime_check_plan', return_value=technical), patch(
                'app.services.agent_ui_build_contract.project_agent_ui_build_contracts', return_value={page_id: contract}):
            original = acceptance_contexts(root)[0]['inputs']
            disk.source.write_text('changed-source')
            changed = acceptance_contexts(root)[0]['inputs']
            self.assertNotEqual(original['frontend'], changed['frontend'])
            lock = root / 'frontend/pnpm-lock.yaml'
            lock.write_text(lock.read_text() + '\n')
            self.assertNotEqual(changed['lock'], acceptance_contexts(root)[0]['inputs']['lock'])
            package = root / 'frontend/node_modules/@za38.03/agent-ui-runtime/index.js'
            package.write_text('same-version-different-bytes')
            self.assertNotEqual(changed['packages'], acceptance_contexts(root)[0]['inputs']['packages'])
            application_path.write_text(json.dumps({**application, 'newSetting': True}))
            self.assertNotEqual(original['config'], acceptance_contexts(root)[0]['inputs']['config'])
            self.assertNotIn('same-version-different-bytes', json.dumps(acceptance_contexts(root)))

    def test_missing_product_is_not_silently_classified_as_no_agent(self) -> None:
        """正式文件缺失不能通过空用例集合绕过最终验收。"""
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, '产品规划缺失'):
                acceptance_matrix(Path(directory).resolve())
